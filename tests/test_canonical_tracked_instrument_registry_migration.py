import unittest
from pathlib import Path


MIGRATION_PATH = Path(
    "supabase/migrations/20260903120000_canonical_tracked_instruments.sql"
)


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

    def test_upsert_merges_sources_and_reactivates_without_creating_events(self) -> None:
        self.assertIn("create or replace function public.upsert_tracked_instrument", self.lower_sql)
        self.assertIn("on conflict (instrument_key, market_key) do update", self.lower_sql)
        self.assertIn("tracked_instruments.sources || excluded.sources[1]", self.lower_sql)
        self.assertIn("active = true", self.lower_sql)
        self.assertNotIn("insert into public.tracked_market_events", self.lower_sql)
        self.assertNotIn("insert into public.event_expectations", self.lower_sql)
        self.assertNotIn("strategy", self.lower_sql)
        self.assertNotIn("broker", self.lower_sql)
        self.assertNotIn("trading_task", self.lower_sql)

    def test_registry_is_service_role_only_and_source_values_are_bounded(self) -> None:
        self.assertIn("alter table public.tracked_instruments enable row level security", self.lower_sql)
        self.assertIn(
            "revoke all on table public.tracked_instruments from public, anon, authenticated",
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
