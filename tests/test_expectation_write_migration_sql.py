"""Executes the real migration SQL for the strategy-draft write path
against a real Postgres instance - not source-text assertions.

This is deliberately opt-in and skips by default: most environments
running `pytest tests/` (including this repo's own default dev setup)
have no Postgres reachable, and the rest of this test suite's SQL-related
coverage (tests/test_deploy_workflow.py's SchemaGateFileConsistencyTests)
exists specifically because the deploy job's self-hosted runner cannot
execute SQL either. Set MARKETAI_TEST_DATABASE_URL to a libpq connection
string for a scratch Postgres database (e.g. a `services: postgres:`
container in CI, or a local `postgresql://postgres:postgres@localhost/postgres`)
to actually run these - a good CI setup should.

What this proves, that no amount of source-text parsing can:
insert_next_expectation_version()'s and verify_strategy_draft_schema()'s
RETURNS TABLE shapes actually changed in 20260823090000_expectation_write_
atomic_response_and_schema_version.sql - `create or replace function`
cannot do that for an existing same-signature function (Postgres rejects
it: "cannot change return type of existing function"), so this migration
needs an explicit `drop function` before recreating either one. It also
proves the whole file is genuinely one atomic transaction: a failure
partway through must roll back *everything*, including
strategy_draft_schema_version() (the schema-gate marker), never leaving a
Supabase project in a state where the marker says "up to date" while
insert_next_expectation_version() is still an older, un-migrated version -
which is exactly what an earlier, unwrapped draft of this migration was
found to do when it failed partway through in manual testing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"

EVENT_STRATEGY_APPROVALS_MIGRATION = (
    MIGRATIONS_DIR / "20260821090000_event_strategy_approvals.sql"
)
SHARED_LOCK_MIGRATION = MIGRATIONS_DIR / "20260821140000_shared_expectation_version_lock.sql"
VERIFY_MIGRATION = MIGRATIONS_DIR / "20260822090000_verify_strategy_draft_schema.sql"
EXPECTATION_WRITE_V2_MIGRATION = (
    MIGRATIONS_DIR / "20260823090000_expectation_write_atomic_response_and_schema_version.sql"
)

# A minimal stand-in for the pre-existing production tables these
# migrations depend on (market_events, event_expectation_versions) - not
# tracked as a migration in this repo (see docs/event_configuration_storage.md),
# so a real base schema has to be constructed here to test against.
BASE_SCHEMA_SQL = """
drop schema if exists public cascade;
create schema public;

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
  'hays-fy2026-results', 1, 'manual',
  '{"fy27_operating_profit_pre_exceptional_gbp_m": 55.6}'::jsonb,
  '{"bull_fy27_operating_profit_gbp_m": 60.0}'::jsonb,
  'seed'
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
class ExpectationWriteMigrationSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = _database_url()

    def setUp(self) -> None:
        # Every test starts from a completely clean `public` schema and
        # the "old shape" baseline (migrations 1-3, before this round's
        # fix) - each test then applies (a correct or deliberately broken
        # copy of) migration 4 on top of that and checks the result.
        self._run_sql(BASE_SCHEMA_SQL)
        for migration in (
            EVENT_STRATEGY_APPROVALS_MIGRATION,
            SHARED_LOCK_MIGRATION,
            VERIFY_MIGRATION,
        ):
            result = self._run_sql_file(migration)
            self.assertEqual(
                result.returncode, 0, f"baseline migration {migration.name} failed: {result.stderr}"
            )

    def _psql(self, *extra_args: str) -> list[str]:
        return ["psql", self.database_url, "-v", "ON_ERROR_STOP=1", *extra_args]

    def _run_sql(self, sql: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._psql("-c", sql), capture_output=True, text=True, timeout=30
        )

    def _run_sql_file(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._psql("-f", str(path)), capture_output=True, text=True, timeout=30
        )

    def _scalar(self, sql: str) -> str:
        result = subprocess.run(
            ["psql", self.database_url, "-tAc", sql],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _out_columns(self, function_name: str) -> str:
        # A RETURNS TABLE(...)/OUT-parameter shape isn't exposed through
        # information_schema the way a real table's columns are - it's
        # part of pg_proc's own argument list (proargnames/proargmodes),
        # with mode 'o' (OUT) or 't' (TABLE column) marking the returned
        # ones. unnest() on two parallel arrays zips them together in
        # their shared, original position order, so filtering by mode
        # here still yields the OUT columns in their declared order.
        return self._scalar(
            "select array_to_string(array(select pn from "
            "unnest(p.proargnames, p.proargmodes) as u(pn, pm) "
            "where pm in ('o', 't')), ',') "
            f"from pg_proc p where p.proname = '{function_name}'"
        )

    # -- baseline sanity: the "before" state is genuinely representative --

    def test_baseline_has_the_old_return_shapes(self) -> None:
        self.assertEqual(
            self._out_columns("insert_next_expectation_version"),
            "new_version,created_at",
        )
        self.assertEqual(
            self._out_columns("verify_strategy_draft_schema"),
            "event_strategy_approvals_table_exists,"
            "approve_strategy_draft_function_exists,"
            "insert_next_expectation_version_function_exists",
        )

    # -- the actual migration: succeeds, and produces the new shapes -------

    def test_new_migration_applies_cleanly_and_produces_the_new_shapes(self) -> None:
        result = self._run_sql_file(EXPECTATION_WRITE_V2_MIGRATION)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(
            self._out_columns("insert_next_expectation_version"),
            "out_version,out_created_at,out_instrument,out_event_name,out_scheduled_date,"
            "out_source_name,out_source_url,out_source_as_of,out_consensus,"
            "out_important_kpis,out_bull_case,out_base_case,out_bear_case,out_triggers,"
            "out_invalidation_conditions",
        )

        self.assertEqual(
            self._out_columns("verify_strategy_draft_schema"),
            "event_strategy_approvals_table_exists,"
            "approve_strategy_draft_function_exists,"
            "insert_next_expectation_version_function_exists,"
            "schema_version_matches",
        )

        # The gate itself must report every check true post-migration.
        self.assertEqual(
            self._scalar("select schema_version_matches from public.verify_strategy_draft_schema()"),
            "t",
        )
        self.assertEqual(
            self._scalar("select public.strategy_draft_schema_version()"), "1"
        )

        # And the new RPC must actually work end to end, not just declare
        # the right column names - a real partial-patch call must merge
        # against the seeded version-1 row and return the merged result.
        version = self._scalar(
            "select out_version from public.insert_next_expectation_version("
            "'hays-fy2026-results', null, null, null, null, null, null, null, null, "
            "'{\"bull_fy27_operating_profit_gbp_m\": 99.0}'::jsonb, null, "
            "'raise bull threshold')"
        )
        self.assertEqual(version, "2")

        consensus_preserved = self._scalar(
            "select consensus->>'fy27_operating_profit_pre_exceptional_gbp_m' "
            "from public.event_expectation_versions where event_id = 'hays-fy2026-results' "
            "and version = 2"
        )
        self.assertEqual(consensus_preserved, "55.6")

        triggers_updated = self._scalar(
            "select triggers->>'bull_fy27_operating_profit_gbp_m' "
            "from public.event_expectation_versions where event_id = 'hays-fy2026-results' "
            "and version = 2"
        )
        self.assertEqual(triggers_updated, "99.0")

    # -- atomicity: a failure partway through commits nothing at all -------

    def test_a_failure_partway_through_leaves_nothing_committed(self) -> None:
        # Mirrors exactly the failure mode found in manual testing: an
        # earlier, unwrapped draft of this migration left
        # strategy_draft_schema_version() created and committed even
        # though insert_next_expectation_version() itself never actually
        # finished being redefined, because the file wasn't running as one
        # transaction. Renaming verify_strategy_draft_schema() partway
        # through this copy reproduces a failure in the same *position*
        # in the file (after the marker and the first RPC's drop+create
        # have already run) without needing to hand-craft a syntax error.
        source = EXPECTATION_WRITE_V2_MIGRATION.read_text(encoding="utf-8")
        broken = source.replace(
            "create function public.verify_strategy_draft_schema()",
            "create function public.verify_strategy_draft_schema_INJECTED_FAILURE()",
            1,
        )
        self.assertNotEqual(broken, source, "expected replacement text not found in migration")

        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
            handle.write(broken)
            broken_path = Path(handle.name)
        try:
            result = self._run_sql_file(broken_path)
        finally:
            broken_path.unlink(missing_ok=True)

        self.assertNotEqual(result.returncode, 0)

        # Nothing from this failed migration may be visible - not the
        # marker, not either RPC's new shape.
        marker_exists = self._scalar(
            "select exists(select 1 from pg_proc where proname = 'strategy_draft_schema_version')"
        )
        self.assertEqual(marker_exists, "f")

        self.assertEqual(
            self._out_columns("insert_next_expectation_version"), "new_version,created_at"
        )
        self.assertEqual(
            self._out_columns("verify_strategy_draft_schema"),
            "event_strategy_approvals_table_exists,"
            "approve_strategy_draft_function_exists,"
            "insert_next_expectation_version_function_exists",
        )


if __name__ == "__main__":
    unittest.main()
