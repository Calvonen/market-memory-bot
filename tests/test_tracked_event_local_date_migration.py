from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260902109000_tracked_event_local_date.sql"
SCHEMA_GATE = ROOT / "scripts/verify_supabase_schema.py"


class TrackedEventLocalDateMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.schema_gate = SCHEMA_GATE.read_text(encoding="utf-8")

    def test_adds_date_only_column_to_canonical_tracked_event(self) -> None:
        self.assertIn("add column if not exists event_date date null", self.sql)
        self.assertIn("column_name = 'event_date'", self.sql)
        self.assertIn("data_type = 'date'", self.sql)

    def test_backfills_only_from_calendar_local_date(self) -> None:
        self.assertIn("set event_date = c.scheduled_date", self.sql)
        self.assertIn("t.calendar_event_id = c.id", self.sql)
        self.assertNotIn("event_at::date", self.sql)
        self.assertNotIn("date(event_at", self.sql.lower())

    def test_calendar_promotion_persists_and_checks_local_date(self) -> None:
        self.assertIn("event_date = calendar_row.scheduled_date", self.sql)
        self.assertIn("calendar_runtime_event_date_conflict", self.sql)
        self.assertIn("perform * from public.ensure_calendar_release_shell", self.sql)

    def test_schema_gate_requires_new_runtime_version_and_column(self) -> None:
        self.assertIn("select 11;", self.sql)
        self.assertIn(
            '"tracked_market_event_event_date_column_exists",',
            self.schema_gate,
        )
        # This migration introduced runtime schema v11. Later canonical release-shell
        # migrations legitimately advance the deploy gate, so this regression must
        # require at least v11 rather than pinning the current global gate to v11.
        marker = "REQUIRED_TRACKED_EVENT_RUNTIME_SCHEMA_VERSION = "
        line = next(
            line for line in self.schema_gate.splitlines() if line.startswith(marker)
        )
        self.assertGreaterEqual(int(line.removeprefix(marker)), 11)


if __name__ == "__main__":
    unittest.main()
