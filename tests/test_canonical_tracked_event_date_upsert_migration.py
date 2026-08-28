from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260902110000_canonical_tracked_event_date_upsert.sql"


class CanonicalTrackedEventDateUpsertMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_exposes_producer_neutral_atomic_writer(self) -> None:
        self.assertIn("create or replace function public.upsert_canonical_tracked_market_event(", self.sql)
        self.assertIn("input_event_date date", self.sql)
        self.assertIn("from public.upsert_tracked_market_event(", self.sql)
        self.assertIn("for update", self.sql.lower())

    def test_requires_explicit_local_event_date(self) -> None:
        self.assertIn("tracked_market_event_date_required", self.sql)
        self.assertNotIn("input_event_at::date", self.sql)
        self.assertNotIn("date(input_event_at", self.sql.lower())

    def test_fails_closed_on_date_conflict_or_late_backfill(self) -> None:
        self.assertIn("tracked_market_event_date_conflict", self.sql)
        self.assertIn("tracked_market_event_date_locked", self.sql)
        self.assertIn("saved_row.status <> 'tracked'", self.sql)
        self.assertIn("saved_row.reference_price is not null", self.sql)
        self.assertIn("saved_row.started_at is not null", self.sql)
        self.assertIn("saved_row.pre_event_market_context is not null", self.sql)

    def test_pre_event_context_locks_missing_event_date(self) -> None:
        lock_block = (
            "if saved_row.status <> 'tracked'\n"
            "       or saved_row.reference_price is not null\n"
            "       or saved_row.started_at is not null\n"
            "       or saved_row.pre_event_market_context is not null then"
        )
        self.assertIn(lock_block, self.sql)

    def test_exact_retry_preserves_existing_date(self) -> None:
        conflict = "saved_row.event_date is not null\n     and saved_row.event_date is distinct from input_event_date"
        self.assertIn(conflict, self.sql)
        self.assertIn("if saved_row.event_date is null then", self.sql)

    def test_writer_is_service_role_only(self) -> None:
        self.assertIn("revoke all on function public.upsert_canonical_tracked_market_event", self.sql)
        self.assertIn("grant execute on function public.upsert_canonical_tracked_market_event", self.sql)
        self.assertIn("to service_role", self.sql)


if __name__ == "__main__":
    unittest.main()
