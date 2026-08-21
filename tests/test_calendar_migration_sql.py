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


if __name__ == "__main__":
    unittest.main()
