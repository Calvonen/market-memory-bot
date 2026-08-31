from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260903156000_completed_broker_attempt_recovery.sql"


class CompletedBrokerAttemptRecoveryMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_completed_attempt_is_reusable_after_claim_token_changes(self) -> None:
        completed = self.sql.index("if attempt_row.status = 'completed' then")
        token_check = self.sql.index("if attempt_row.claim_token <> input_claim_token then")
        self.assertLess(completed, token_check)
        self.assertIn("claim_row.claim_token <> input_claim_token", self.sql)

    def test_claim_wrapper_recovers_completed_attempt_before_returning(self) -> None:
        self.assertIn("recover_completed_event_paper_broker_attempt", self.sql)
        self.assertIn("save_event_paper_trade_result_for_task", self.sql)
        self.assertIn("'status', 'paper_executed'", self.sql)
        self.assertIn("'paper_order', attempt_row.order_payload", self.sql)
        self.assertIn("select * into claim_row\n      from public.event_paper_trade_event_claims", self.sql)

    def test_recovery_requires_current_exact_task_analysis_and_lease(self) -> None:
        self.assertIn("claim_row.task_id <> input_task_id", self.sql)
        self.assertIn("claim_row.analysis_id <> input_analysis_id", self.sql)
        self.assertIn("claim_row.claim_token <> input_claim_token", self.sql)
        self.assertIn("claim_row.lease_expires_at <= now_value", self.sql)
        self.assertIn("attempt_row.task_id <> input_task_id", self.sql)
        self.assertIn("attempt_row.analysis_id <> input_analysis_id", self.sql)
        self.assertIn("attempt_row.expectation_version <> input_expectation_version", self.sql)

    def test_started_attempt_remains_fail_closed_on_process_change(self) -> None:
        self.assertIn("return query select false, attempt_row.status, null::jsonb", self.sql)
        self.assertIn("if attempt_row.execution_token <> input_execution_token then", self.sql)


if __name__ == "__main__":
    unittest.main()
