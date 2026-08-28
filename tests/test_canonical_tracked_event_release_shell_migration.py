from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260902112000_canonical_tracked_event_release_shell.sql"


class CanonicalTrackedEventReleaseShellMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_defines_producer_neutral_release_shell(self) -> None:
        self.assertIn("create or replace function public.ensure_tracked_event_release_shell(", self.sql)
        self.assertIn("from public.tracked_market_events", self.sql)
        self.assertIn("tracked_row.event_date", self.sql)
        self.assertNotIn("tracked_row.event_at::date", self.sql)
        self.assertNotIn("date(tracked_row.event_at", self.sql.lower())

    def test_calendar_bound_rows_preserve_identity_and_validate_binding(self) -> None:
        self.assertIn("if tracked_row.calendar_event_id is not null then", self.sql)
        self.assertIn("from public.calendar_events", self.sql)
        self.assertIn("tracked_release_calendar_binding_identity_conflict", self.sql)
        self.assertIn("calendar_row.scheduled_date is distinct from tracked_row.event_date", self.sql)
        self.assertIn("calendar_row.event_type is distinct from tracked_row.kind", self.sql)
        self.assertIn("release_event_id := 'calendar:' || calendar_row.id::text", self.sql)

    def test_calendar_backfill_does_not_depend_on_watchlist_status(self) -> None:
        self.assertNotIn("from public.ensure_calendar_release_shell(tracked_row.calendar_event_id)", self.sql)
        self.assertIn("calendar rows that have advanced past", self.sql)

    def test_calendarless_rows_use_tracked_event_identity(self) -> None:
        self.assertIn("release_event_id := 'tracked:' || tracked_row.id::text", self.sql)
        self.assertIn("tracked_row.instrument || ' ' || tracked_row.kind", self.sql)
        self.assertIn("'tracked:' || tracked_row.source || ':automatic-release-shell'", self.sql)

    def test_earnings_date_write_creates_shell_atomically(self) -> None:
        self.assertIn("after insert or update of event_date on public.tracked_market_events", self.sql)
        self.assertIn("when (new.kind = 'earnings' and new.event_date is not null)", self.sql)
        self.assertIn("perform * from public.ensure_tracked_event_release_shell(new.id)", self.sql)

    def test_existing_explicit_dates_are_backfilled_without_utc_derivation(self) -> None:
        self.assertIn("where kind = 'earnings'", self.sql)
        self.assertIn("and event_date is not null", self.sql)
        self.assertIn("perform * from public.ensure_tracked_event_release_shell(target.id)", self.sql)
        self.assertNotIn("event_at::date", self.sql)

    def test_shell_fails_closed_on_identity_conflict(self) -> None:
        self.assertIn("tracked_release_shell_identity_conflict", self.sql)
        self.assertIn("tracked_event_release_date_required", self.sql)
        self.assertIn("tracked_event_not_release_shell_eligible", self.sql)

    def test_schema_gate_requires_canonical_shell_and_trigger(self) -> None:
        self.assertIn("ensure_tracked_event_release_shell_function_exists boolean", self.sql)
        self.assertIn("tracked_event_release_shell_trigger_exists boolean", self.sql)
        self.assertIn("tracked_market_events_release_shell_after_date_write", self.sql)
        self.assertIn("select 12;", self.sql)


if __name__ == "__main__":
    unittest.main()
