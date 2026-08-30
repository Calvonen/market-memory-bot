from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260903150000_canonical_trading_tasks.sql"


class TradingTaskMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text()

    def test_task_is_separate_from_tracking_and_requires_explicit_state(self) -> None:
        self.assertIn("create table if not exists public.trading_tasks", self.sql)
        self.assertIn("references public.tracked_market_events(id) on delete restrict", self.sql)
        self.assertIn("state in ('pending', 'approved', 'cancelled')", self.sql)
        self.assertNotIn("insert into public.tracked_market_events", self.sql)
        self.assertNotIn("insert into public.tracked_instruments", self.sql)

    def test_create_rpc_validates_canonical_event_and_instrument_identity(self) -> None:
        self.assertIn("create or replace function public.create_trading_task", self.sql)
        self.assertIn("trading_task_event_identity_mismatch", self.sql)
        self.assertIn("trading_task_instrument_mismatch", self.sql)
        self.assertIn("'calendar:' || event_row.calendar_event_id::text", self.sql)
        self.assertIn("'tracked:' || event_row.id::text", self.sql)

    def test_creation_retry_returns_existing_active_task(self) -> None:
        self.assertIn("when unique_violation then", self.sql)
        self.assertIn("where tracked_event_id = input_tracked_event_id", self.sql)
        self.assertIn("and mode = mode_value", self.sql)
        self.assertIn("and state in ('pending', 'approved')", self.sql)
        self.assertIn("return created", self.sql)
        self.assertIn("trading_task_creation_conflict", self.sql)

    def test_approval_is_separate_from_creation(self) -> None:
        self.assertIn("'pending', actor", self.sql)
        self.assertIn("create or replace function public.approve_trading_task", self.sql)
        self.assertIn("where id = input_task_id and state = 'pending'", self.sql)

    def test_cancelled_task_releases_slot_for_new_explicit_request(self) -> None:
        self.assertIn("trading_tasks_active_event_mode_uidx", self.sql)
        self.assertIn("where state in ('pending', 'approved')", self.sql)
        self.assertNotIn("unique (tracked_event_id, mode)", self.sql)

    def test_writes_are_service_role_rpc_only(self) -> None:
        self.assertIn(
            "revoke all on table public.trading_tasks from public, anon, authenticated, service_role",
            self.sql,
        )
        self.assertIn("grant select on table public.trading_tasks to service_role", self.sql)
        self.assertNotIn("grant insert", self.sql)
        self.assertNotIn("grant update", self.sql)
        self.assertIn("grant execute on function public.create_trading_task", self.sql)
        self.assertIn("grant execute on function public.approve_trading_task", self.sql)
        self.assertIn("grant execute on function public.cancel_trading_task", self.sql)


if __name__ == "__main__":
    unittest.main()
