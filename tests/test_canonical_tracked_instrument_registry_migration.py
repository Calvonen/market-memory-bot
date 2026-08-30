import re
import unittest
from pathlib import Path


MIGRATION_PATH = Path(
    "supabase/migrations/20260903120000_canonical_tracked_instruments.sql"
)


def _function_body(sql: str, signature: str) -> str:
    start = sql.index(signature)
    body_start = sql.index("as $$", start)
    end = sql.index("\n$$;", body_start)
    return sql[body_start:end].lower()


class CanonicalTrackedInstrumentRegistryMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION_PATH.read_text(encoding="utf-8")
        cls.lower_sql = cls.sql.lower()

    def test_registry_has_stable_normalized_instrument_market_identity(self) -> None:
        self.assertIn("create table public.tracked_instruments", self.lower_sql)
        self.assertIn("instrument_key text generated always as", self.lower_sql)
        self.assertIn("market_key text generated always as", self.lower_sql)
        self.assertIn("unique (instrument_key, market_key)", self.lower_sql)

    def test_upsert_merges_sources_and_reactivates_without_downstream_trading_writes(self) -> None:
        self.assertIn("create or replace function public.upsert_tracked_instrument", self.lower_sql)
        upsert_body = _function_body(
            self.sql,
            "create or replace function public.upsert_tracked_instrument(",
        )
        self.assertIn("on conflict (instrument_key, market_key) do update", upsert_body)
        self.assertIn("tracked_instruments.sources || excluded.sources[1]", upsert_body)
        self.assertIn("active = true", upsert_body)

        # The canonical instrument upsert may mutate only its own registry table.
        # Because the SECURITY DEFINER function uses search_path=public,pg_temp,
        # downstream public surfaces are dangerous both schema-qualified and
        # unqualified. Enforce the boundary structurally by allowlisting every
        # DML target in this function body instead of trying to enumerate every
        # downstream tracked-event/workflow table that exists now or later.
        dml_targets = [
            target
            for _verb, target in re.findall(
                r"\b(insert\s+into|update|delete\s+from|truncate(?:\s+table)?)\s+([a-z_][a-z0-9_.]*)",
                upsert_body,
            )
        ]
        self.assertTrue(dml_targets)
        for target in dml_targets:
            self.assertIn(target, ("tracked_instruments", "public.tracked_instruments"))

        downstream_surface = upsert_body.replace("public.tracked_instruments", "")
        self.assertNotIn("public.", downstream_surface)

        for forbidden_identifier in (
            "tracked_event",
            "tracked_market_event",
            "market_event",
            "calendar_event",
            "event_expectation",
            "expectation_version",
            "release_shell",
            "strategy",
            "risk",
            "broker",
            "trading_task",
            "trading_",
            "paper_trade",
            "paper_run",
        ):
            self.assertNotIn(forbidden_identifier, upsert_body)

        for forbidden_sql in (
            "perform ",
            "call ",
            "execute ",
        ):
            self.assertNotIn(forbidden_sql, upsert_body)

    def test_registry_mutations_are_rpc_only_and_source_values_are_bounded(self) -> None:
        self.assertIn("alter table public.tracked_instruments enable row level security", self.lower_sql)
        self.assertIn(
            "revoke all on table public.tracked_instruments from public, anon, authenticated, service_role",
            self.lower_sql,
        )
        self.assertIn("grant select on table public.tracked_instruments to service_role", self.lower_sql)
        self.assertNotIn(
            "grant select, insert, update on table public.tracked_instruments to service_role",
            self.lower_sql,
        )
        self.assertIn(
            "grant execute on function public.upsert_tracked_instrument(text, text, text, text, text)",
            self.lower_sql,
        )
        self.assertIn("'scanner', 'calendar', 'manual'", self.lower_sql)
        self.assertIn("tracked_instrument_invalid_source", self.lower_sql)

    def test_schema_gate_advances_runtime_to_16_and_exposes_registry_verifier(self) -> None:
        self.assertIn("verify_tracked_instrument_registry_schema", self.lower_sql)
        self.assertIn("tracked_instrument_registry_schema_version", self.lower_sql)
        self.assertIn("select 16;", self.lower_sql)


if __name__ == "__main__":
    unittest.main()
