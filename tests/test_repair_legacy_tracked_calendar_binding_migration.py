from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260902111500_repair_legacy_tracked_calendar_binding.sql"
NEXT_MIGRATION = ROOT / "supabase/migrations/20260902112000_canonical_tracked_event_release_shell.sql"


class RepairLegacyTrackedCalendarBindingMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_runs_before_release_shell_backfill(self) -> None:
        self.assertLess(MIGRATION.name, NEXT_MIGRATION.name)

    def test_repairs_only_unambiguous_terminal_non_calendar_identity(self) -> None:
        self.assertIn("set calendar_event_id = null", self.sql)
        self.assertIn("t.status in ('completed', 'failed', 'cancelled')", self.sql)
        self.assertIn("upper(replace(c.instrument, ' ', '')) = t.instrument", self.sql)
        self.assertIn("c.event_type = t.kind", self.sql)
        self.assertIn("c.scheduled_date = t.event_date", self.sql)
        self.assertIn("c.source is distinct from t.source", self.sql)
        self.assertIn("t.external_key not like 'calendar:%'", self.sql)

    def test_detach_requires_no_dependent_release_or_execution_state(self) -> None:
        for table_name in (
            "market_events",
            "event_expectation_versions",
            "event_official_release_sources",
            "event_official_release_source_audit",
            "event_source_documents",
            "event_ai_analyses",
            "event_ingestion_runs",
            "event_strategy_approvals",
            "event_paper_trade_event_claims",
            "event_paper_trade_runs",
        ):
            self.assertIn(f"from public.{table_name} s", self.sql)
        self.assertIn("where s.event_id = ('calendar:' || c.id::text)", self.sql)

    def test_remaining_conflicts_abort_with_ids_and_reasons(self) -> None:
        self.assertIn("string_agg(", self.sql)
        self.assertIn("calendar_event_missing", self.sql)
        self.assertIn("instrument_mismatch", self.sql)
        self.assertIn("kind_mismatch", self.sql)
        self.assertIn("event_date_mismatch", self.sql)
        self.assertIn("source_mismatch", self.sql)
        self.assertIn("external_key_mismatch", self.sql)
        self.assertIn("tracked_release_shell_legacy_binding_conflicts", self.sql)
        self.assertIn("raise exception", self.sql)


if __name__ == "__main__":
    unittest.main()
