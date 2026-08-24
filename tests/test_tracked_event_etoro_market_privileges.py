from pathlib import Path
import unittest


class TrackedEventEtoroMarketPrivilegeMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902091000_tracked_event_etoro_market_capture.sql"
        ).read_text(encoding="utf-8")

    def test_service_role_cannot_directly_update_resolved_market(self) -> None:
        self.assertIn(
            "revoke update on table public.tracked_market_events from service_role;",
            self.sql,
        )
        self.assertIn("a.attname <> 'resolved_etoro_market'", self.sql)
        self.assertIn("grant update (%s) on table public.tracked_market_events to service_role", self.sql)

    def test_capture_uses_privileged_rpc_and_immutable_table_guard(self) -> None:
        self.assertIn(
            "create or replace function public.capture_tracked_market_event_resolved_market(",
            self.sql,
        )
        self.assertIn("security definer", self.sql)
        self.assertIn("set search_path = pg_catalog, public", self.sql)
        self.assertIn(
            "create trigger guard_tracked_market_event_resolved_market",
            self.sql,
        )
        self.assertIn(
            "tracked_market_event_resolved_market_immutable",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
