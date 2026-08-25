from pathlib import Path
import unittest


class PreEventStaleContextFailGateSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902098000_pre_event_stale_context_fail_gate.sql"
        ).read_text(encoding="utf-8")

    def test_failure_is_version_bound_and_locked(self) -> None:
        self.assertIn("for update;", self.sql)
        self.assertIn(
            "existing_row.updated_at is distinct from input_expected_updated_at",
            self.sql,
        )
        self.assertIn("tracked_market_event_version_conflict", self.sql)

    def test_only_unreferenced_tracked_rows_with_context_can_fail(self) -> None:
        self.assertIn("existing_row.status <> 'tracked'", self.sql)
        self.assertIn("existing_row.reference_price is not null", self.sql)
        self.assertIn("existing_row.pre_event_market_context is null", self.sql)
        self.assertIn("tracked_market_event_not_stale_context_failable", self.sql)

    def test_rpc_is_invoker_only_and_schema_becomes_v7(self) -> None:
        self.assertIn("security invoker", self.sql.lower())
        self.assertIn(
            "revoke all on function public.fail_tracked_market_event_stale_context_if_current",
            self.sql,
        )
        self.assertIn("grant execute on function public.fail_tracked_market_event_stale_context_if_current", self.sql)
        self.assertIn("select 7;", self.sql)


if __name__ == "__main__":
    unittest.main()
