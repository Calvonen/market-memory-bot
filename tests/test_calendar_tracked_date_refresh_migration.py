from pathlib import Path
import unittest


MIGRATION = Path(
    "supabase/migrations/20260902099000_calendar_tracked_date_refresh_gate.sql"
)


class CalendarTrackedDateRefreshMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_tracked_watchlist_row_is_only_locked_after_runtime_binding(self) -> None:
        self.assertIn("existing_row.status not in ('candidate', 'tracked')", self.sql)
        self.assertIn("existing_row.status = 'tracked'", self.sql)
        self.assertIn("from public.tracked_market_events t", self.sql)
        self.assertIn("where t.calendar_event_id = existing_row.id", self.sql)

    def test_runtime_bound_row_returns_skipped_locked(self) -> None:
        gate_start = self.sql.index("if existing_row.status not in ('candidate', 'tracked')")
        gate_end = self.sql.index("end if;", gate_start)
        gate = self.sql[gate_start:gate_end]
        self.assertIn("'skipped_locked'::text", gate)

    def test_calendar_upsert_contract_version_is_bumped(self) -> None:
        self.assertIn("select 3;", self.sql)
        self.assertIn("public.calendar_candidate_upsert_version() = 3", self.sql)

    def test_provider_correction_preserves_tracked_status(self) -> None:
        update_start = self.sql.index("update public.calendar_events")
        update_end = self.sql.index("returning * into new_row;", update_start)
        update_sql = self.sql[update_start:update_end]
        self.assertIn("scheduled_date = input_scheduled_date", update_sql)
        self.assertNotIn("status =", update_sql)


if __name__ == "__main__":
    unittest.main()
