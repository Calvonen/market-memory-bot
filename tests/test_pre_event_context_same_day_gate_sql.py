from pathlib import Path
import unittest


class PreEventContextSameDayGateSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902097000_pre_event_context_allow_same_day_session.sql"
        ).read_text(encoding="utf-8")
        base_start = cls.sql.index(
            "create or replace function public.capture_tracked_market_event_pre_event_context(\n"
        )
        validated_start = cls.sql.index(
            "create or replace function public.capture_tracked_market_event_pre_event_context_validated(\n"
        )
        cls.base = cls.sql[base_start:validated_start]
        cls.validated = cls.sql[validated_start:]

    def test_base_rpc_still_rejects_same_day_without_close_proof(self) -> None:
        self.assertIn("if snapshot_session_date >= event_local_date then", self.base)
        self.assertNotIn("input_session_close", self.base)

    def test_validated_rpc_allows_same_day_date_only_behind_proof(self) -> None:
        self.assertIn("if snapshot_session_date > event_local_date then", self.validated)
        self.assertIn("input_session_close timestamptz", self.validated)

    def test_close_must_be_strictly_before_event(self) -> None:
        self.assertIn("if input_session_close >= existing_row.event_at then", self.validated)
        self.assertNotIn("if input_session_close > existing_row.event_at then", self.validated)
        self.assertIn("pre_event_market_context_session_not_closed_before_event", self.validated)

    def test_close_must_have_happened_by_database_clock(self) -> None:
        self.assertIn("if input_session_close > pg_catalog.clock_timestamp() then", self.validated)
        self.assertIn("pre_event_market_context_session_not_closed_yet", self.validated)

    def test_close_is_bound_to_snapshot_local_session_date(self) -> None:
        self.assertIn("session_close_local_date <> snapshot_session_date", self.validated)
        self.assertIn("pre_event_market_context_session_close_mismatch", self.validated)

    def test_exact_retry_precedes_deadline_and_proof_gates(self) -> None:
        retry = self.validated.index(
            "if existing_row.pre_event_market_context = input_pre_event_market_context then"
        )
        deadline = self.validated.index(
            "if pg_catalog.clock_timestamp() >= existing_row.event_at then"
        )
        proof = self.validated.index("if input_session_close >= existing_row.event_at then")
        self.assertLess(retry, deadline)
        self.assertLess(retry, proof)

    def test_validated_rpc_is_service_role_only_and_security_invoker(self) -> None:
        self.assertIn("security invoker", self.validated)
        self.assertIn(
            "revoke all on function public.capture_tracked_market_event_pre_event_context_validated(",
            self.sql,
        )
        self.assertIn(
            ") to service_role;",
            self.sql,
        )

    def test_schema_version_is_six_and_verifier_requires_validated_rpc(self) -> None:
        self.assertIn("select 6;", self.sql)
        self.assertIn(
            "capture_tracked_market_event_pre_event_context_validated_function_exists boolean",
            self.sql,
        )
        self.assertIn(
            "public.capture_tracked_market_event_pre_event_context_validated(uuid,jsonb,text,text,timestamptz,timestamptz)",
            self.sql,
        )

    def test_migration_is_transactional(self) -> None:
        self.assertIn("\nbegin;\n", self.sql)
        self.assertTrue(self.sql.rstrip().endswith("commit;"))


if __name__ == "__main__":
    unittest.main()
