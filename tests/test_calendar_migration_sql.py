"""Executes the real calendar/watchlist migration SQL against a real
Postgres instance - not source-text assertions.

Deliberately opt-in and skips by default, same convention as
tests/test_expectation_write_migration_sql.py: set
MARKETAI_TEST_DATABASE_URL to a libpq connection string for a scratch
Postgres database and ensure `psql` is on PATH to actually run these.

What this proves, that no amount of source-text parsing or in-process
threading (see tests/test_calendar_repository.py's InMemory concurrency
regression test) can: upsert_calendar_candidate()'s first-insert path is
genuinely race-safe against real concurrent Postgres connections, not just
serialized by a single Python-process lock. Before the P2 fix, two
concurrent callers seeing the same brand-new occurrence both found no row
to lock via `select ... for update` and both attempted `insert`, so the
loser raised a raw unique-violation instead of returning an idempotent
result - that failure mode cannot be reproduced by driving one in-process
repository instance from multiple threads, only by racing two real
Postgres transactions against the same live function.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
CALENDAR_MIGRATION = MIGRATIONS_DIR / "20260824090000_calendar_watchlist_events.sql"
CALENDAR_SCHEMA_GATE_MIGRATION = MIGRATIONS_DIR / "20260825090000_calendar_schema_gate.sql"
CALENDAR_UPSERT_VERSION_GATE_MIGRATION = (
    MIGRATIONS_DIR / "20260829090000_distinct_atomic_calendar_candidate_upsert_version.sql"
)
OLDER_CALENDAR_UPSERT_VERSION_GATE_MIGRATION = (
    MIGRATIONS_DIR / "20260827090000_calendar_candidate_upsert_version_gate.sql"
)
EVENT_STRATEGY_APPROVALS_MIGRATION = MIGRATIONS_DIR / "20260821090000_event_strategy_approvals.sql"
SHARED_LOCK_MIGRATION = MIGRATIONS_DIR / "20260821140000_shared_expectation_version_lock.sql"
VERIFY_MIGRATION = MIGRATIONS_DIR / "20260822090000_verify_strategy_draft_schema.sql"
EXPECTATION_WRITE_V2_MIGRATION = (
    MIGRATIONS_DIR / "20260823090000_expectation_write_atomic_response_and_schema_version.sql"
)

BASE_SCHEMA_SQL = """
drop schema if exists public cascade;
create schema public;
create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated;
  end if;
end
$$;
"""

# Minimal stand-in for the pre-existing production tables the
# strategy-draft migrations depend on (see
# tests/test_expectation_write_migration_sql.py's identical BASE_SCHEMA_SQL) -
# needed here only so verify_strategy_draft_schema()'s full migration chain
# (including the strategy-draft objects it has always checked) can actually
# be applied end to end.
STRATEGY_DRAFT_BASE_TABLES_SQL = """
create table public.market_events (
  event_id text primary key,
  instrument text not null,
  event_name text not null,
  scheduled_date date not null,
  updated_at timestamptz not null default now()
);

create table public.event_expectation_versions (
  event_id text not null references public.market_events(event_id) on delete cascade,
  version integer not null,
  source_name text,
  source_url text,
  source_as_of date,
  consensus jsonb not null default '{}'::jsonb,
  important_kpis jsonb not null default '[]'::jsonb,
  bull_case jsonb not null default '[]'::jsonb,
  base_case jsonb not null default '[]'::jsonb,
  bear_case jsonb not null default '[]'::jsonb,
  triggers jsonb not null default '{}'::jsonb,
  invalidation_conditions jsonb not null default '[]'::jsonb,
  change_note text,
  created_at timestamptz not null default now(),
  primary key (event_id, version)
);

insert into public.market_events (event_id, instrument, event_name, scheduled_date)
values ('hays-fy2026-results', 'HAS.L', 'Hays plc FY2026 results', '2026-08-20');

insert into public.event_expectation_versions (
  event_id, version, source_name, consensus, triggers, change_note
) values (
  'hays-fy2026-results', 1, 'manual', '{}'::jsonb, '{}'::jsonb, 'seed'
);
"""


def _database_url() -> str | None:
    return os.environ.get("MARKETAI_TEST_DATABASE_URL")


def _psql_available() -> bool:
    return shutil.which("psql") is not None


@unittest.skipUnless(
    _psql_available() and _database_url(),
    "Set MARKETAI_TEST_DATABASE_URL (a libpq connection string to a scratch "
    "Postgres database) and ensure psql is on PATH to run these tests - "
    "they execute real migration SQL against a real Postgres instance, "
    "which most environments running this suite don't have.",
)
class CalendarMigrationSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = _database_url()

    def setUp(self) -> None:
        self._run_sql(BASE_SCHEMA_SQL)
        result = self._run_sql_file(CALENDAR_MIGRATION)
        self.assertEqual(result.returncode, 0, f"migration failed: {result.stderr}")

    def _psql(self, *extra_args: str) -> list[str]:
        return ["psql", self.database_url, "-v", "ON_ERROR_STOP=1", *extra_args]

    def _run_sql(self, sql: str) -> subprocess.CompletedProcess:
        return subprocess.run(self._psql("-c", sql), capture_output=True, text=True, timeout=30)

    def _run_sql_file(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._psql("-f", str(path)), capture_output=True, text=True, timeout=30
        )

    def _scalar(self, sql: str) -> str:
        result = subprocess.run(
            ["psql", self.database_url, "-tAc", sql], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _upsert(
        self,
        *,
        company_name: str = "Apple Inc",
        instrument: str = "AAPL",
        market: str = "NASDAQ",
        event_type: str = "earnings",
        occurrence_key: str = "2026Q4",
        scheduled_date: str = "2026-10-29",
        source: str = "finnhub",
    ) -> subprocess.CompletedProcess:
        sql = (
            "select out_action from public.upsert_calendar_candidate("
            f"'{company_name}', '{instrument}', '{market}', '{event_type}', "
            f"'{occurrence_key}', '{scheduled_date}', '{source}')"
        )
        return subprocess.run(
            ["psql", self.database_url, "-tAc", sql], capture_output=True, text=True, timeout=30
        )

    # -- P2 fix: concurrent first-insert of the same brand-new occurrence --

    def test_concurrent_first_inserts_of_the_same_new_candidate_never_error_or_duplicate(
        self,
    ) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self._upsert(), range(8)))

        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)

        actions = [r.stdout.strip() for r in results]
        # Exactly one racing call actually inserted the row; every other
        # call observed it as already existing (via the locked
        # read-then-update-or-skip fallback) and reported 'updated' - none
        # raised a unique-violation.
        self.assertEqual(actions.count("inserted"), 1)
        self.assertEqual(actions.count("updated"), 7)

        row_count = self._scalar(
            "select count(*) from public.calendar_events "
            "where instrument = 'AAPL' and event_type = 'earnings' "
            "and source = 'finnhub' and occurrence_key = '2026Q4'"
        )
        self.assertEqual(row_count, "1")

    def test_concurrent_sync_never_regresses_or_silently_overwrites_a_tracked_row(self) -> None:
        # Seed and track one occurrence first.
        first = self._upsert(scheduled_date="2026-10-29")
        self.assertEqual(first.returncode, 0, first.stderr)
        calendar_event_id = self._scalar(
            "select id from public.calendar_events where instrument = 'AAPL' "
            "and occurrence_key = '2026Q4'"
        )
        transition = self._run_sql(
            "select * from public.transition_calendar_event_status("
            f"'{calendar_event_id}', 'candidate', 'tracked')"
        )
        self.assertEqual(transition.returncode, 0, transition.stderr)

        # Race several concurrent syncs against the now-tracked occurrence,
        # each proposing a different date.
        def sync_with_date(day: int) -> subprocess.CompletedProcess:
            return self._upsert(scheduled_date=f"2026-11-{day:02d}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(sync_with_date, range(1, 7)))

        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        actions = {r.stdout.strip() for r in results}
        # Every racing call must observe the row as locked - none may
        # report 'inserted' (a duplicate) or 'updated' (an overwrite).
        self.assertEqual(actions, {"skipped_locked"})

        status = self._scalar(
            f"select status from public.calendar_events where id = '{calendar_event_id}'"
        )
        self.assertEqual(status, "tracked")
        scheduled_date = self._scalar(
            f"select scheduled_date from public.calendar_events where id = '{calendar_event_id}'"
        )
        self.assertEqual(scheduled_date, "2026-10-29")

        row_count = self._scalar(
            "select count(*) from public.calendar_events where instrument = 'AAPL' "
            "and occurrence_key = '2026Q4'"
        )
        self.assertEqual(row_count, "1")


@unittest.skipUnless(
    _psql_available() and _database_url(),
    "Set MARKETAI_TEST_DATABASE_URL (a libpq connection string to a scratch "
    "Postgres database) and ensure psql is on PATH to run these tests - "
    "they execute real migration SQL against a real Postgres instance, "
    "which most environments running this suite don't have.",
)
class CalendarSchemaGateSqlTests(unittest.TestCase):
    """Proves the P2 deploy-gate finding is actually fixed: a Supabase
    project missing the calendar migration must never let
    verify_strategy_draft_schema() (and therefore
    scripts/verify_supabase_schema.py) report success - the exact silent
    gap that would otherwise let the deploy workflow restart the backend
    onto a schema that /api/v1/calendar/* cannot actually run against."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = _database_url()

    def _psql(self, *extra_args: str) -> list[str]:
        return ["psql", self.database_url, "-v", "ON_ERROR_STOP=1", *extra_args]

    def _run_sql(self, sql: str) -> subprocess.CompletedProcess:
        return subprocess.run(self._psql("-c", sql), capture_output=True, text=True, timeout=30)

    def _run_sql_file(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._psql("-f", str(path)), capture_output=True, text=True, timeout=30
        )

    def _scalar(self, sql: str) -> str:
        result = subprocess.run(
            ["psql", self.database_url, "-tAc", sql], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _apply_strategy_draft_chain(self) -> None:
        self._run_sql(BASE_SCHEMA_SQL)
        self._run_sql(STRATEGY_DRAFT_BASE_TABLES_SQL)
        for migration in (
            EVENT_STRATEGY_APPROVALS_MIGRATION,
            SHARED_LOCK_MIGRATION,
            VERIFY_MIGRATION,
            EXPECTATION_WRITE_V2_MIGRATION,
        ):
            result = self._run_sql_file(migration)
            self.assertEqual(
                result.returncode, 0, f"baseline migration {migration.name} failed: {result.stderr}"
            )

    def _verify_row(self) -> dict:
        columns = (
            "event_strategy_approvals_table_exists",
            "approve_strategy_draft_function_exists",
            "insert_next_expectation_version_function_exists",
            "schema_version_matches",
            "calendar_events_table_exists",
            "upsert_calendar_candidate_function_exists",
            "transition_calendar_event_status_function_exists",
            "calendar_candidate_upsert_version_matches",
            "calendar_candidate_upsert_implementation_version",
        )
        result = subprocess.run(
            ["psql", self.database_url, "-F,", "-tAc",
             f"select {', '.join(columns)} from public.verify_strategy_draft_schema()"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = result.stdout.strip().split(",")
        row = dict(zip(columns[:-1], [v == "t" for v in values[:-1]]))
        row[columns[-1]] = int(values[-1])
        return row

    def test_older_version_gate_is_distinguishable_from_latest_atomic_migration(self) -> None:
        self._apply_strategy_draft_chain()
        for migration in (
            CALENDAR_MIGRATION,
            CALENDAR_SCHEMA_GATE_MIGRATION,
            OLDER_CALENDAR_UPSERT_VERSION_GATE_MIGRATION,
        ):
            result = self._run_sql_file(migration)
            self.assertEqual(result.returncode, 0, f"{migration.name} failed: {result.stderr}")

        self.assertEqual(self._scalar("select public.calendar_candidate_upsert_version()"), "1")
        # The verifier installed by 20260827090000 predates the explicit
        # implementation-version result required by the deploy script.
        result = self._run_sql(
            "select calendar_candidate_upsert_implementation_version "
            "from public.verify_strategy_draft_schema()"
        )
        self.assertNotEqual(result.returncode, 0)

        result = self._run_sql_file(CALENDAR_UPSERT_VERSION_GATE_MIGRATION)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self._verify_row()
        self.assertEqual(row["calendar_candidate_upsert_implementation_version"], 2)
        self.assertTrue(all(value is True or value == 2 for value in row.values()), row)

    def test_gate_fails_closed_when_the_calendar_migration_was_never_applied(self) -> None:
        # The deploy-gate migration (20260825090000) is applied on top of
        # only the pre-calendar strategy-draft chain - CALENDAR_MIGRATION
        # (20260824090000) is deliberately skipped, simulating exactly the
        # missed out-of-band-migration scenario the gate exists to catch.
        self._apply_strategy_draft_chain()
        result = self._run_sql_file(CALENDAR_SCHEMA_GATE_MIGRATION)
        self.assertEqual(result.returncode, 0, f"gate migration failed: {result.stderr}")
        result = self._run_sql_file(CALENDAR_UPSERT_VERSION_GATE_MIGRATION)
        # The atomic follow-up cannot attest to an implementation when the
        # calendar table required by that implementation is absent. Its
        # transaction therefore fails closed without publishing the marker.
        self.assertNotEqual(result.returncode, 0)
        marker = self._scalar("select to_regprocedure('public.calendar_candidate_upsert_version()')")
        self.assertEqual(marker, "")

    def test_gate_passes_once_both_calendar_migrations_are_applied(self) -> None:
        self._apply_strategy_draft_chain()
        for migration in (
            CALENDAR_MIGRATION,
            CALENDAR_SCHEMA_GATE_MIGRATION,
            CALENDAR_UPSERT_VERSION_GATE_MIGRATION,
        ):
            result = self._run_sql_file(migration)
            self.assertEqual(result.returncode, 0, f"{migration.name} failed: {result.stderr}")

        row = self._verify_row()
        self.assertTrue(all(row.values()), row)

        # Deliberately skip 20260826090000 above. The version-gate migration
        # must install the behavior its marker attests to, rather than merely
        # letting the unchanged signature of the old body pass verification.
        enriched = self._run_sql(
            "select * from public.upsert_calendar_candidate("
            "'Apple Inc', 'AAPL', 'NASDAQ', 'earnings', '2026Q4', "
            "'2026-10-29', 'finnhub')"
        )
        self.assertEqual(enriched.returncode, 0, enriched.stderr)
        placeholder = self._run_sql(
            "select * from public.upsert_calendar_candidate("
            "'AAPL', 'AAPL', 'Unknown', 'earnings', '2026Q4', "
            "'2026-10-30', 'finnhub')"
        )
        self.assertEqual(placeholder.returncode, 0, placeholder.stderr)
        metadata = self._scalar(
            "select company_name || ',' || market from public.calendar_events "
            "where instrument = 'AAPL' and occurrence_key = '2026Q4'"
        )
        self.assertEqual(metadata, "Apple Inc,NASDAQ")


if __name__ == "__main__":
    unittest.main()
