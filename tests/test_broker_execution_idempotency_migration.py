from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260903155000_broker_execution_idempotency.sql"


class BrokerExecutionIdempotencyMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_task_claim_wrapper_is_security_definer(self) -> None:
        self.assertIn(
            "alter function public.claim_event_paper_run_for_task_v2(text, uuid, uuid, integer, uuid, integer)\n  security definer;",
            self.sql,
        )
        self.assertNotIn(
            "grant execute on function public.claim_event_paper_run_for_task(text, uuid, uuid, integer, uuid, integer)\n  to service_role;",
            self.sql,
        )

    def test_broker_attempt_is_unique_per_task_and_event(self) -> None:
        self.assertIn("create table if not exists public.event_paper_broker_attempts", self.sql)
        self.assertIn("task_id uuid primary key", self.sql)
        self.assertIn("event_paper_broker_attempts_event_uidx", self.sql)
        self.assertIn("on conflict do nothing", self.sql)
        self.assertIn("attempt_row.task_id <> input_task_id", self.sql)
        self.assertIn("attempt_row.execution_token <> input_execution_token", self.sql)

    def test_completed_attempt_is_reusable_but_started_attempt_is_not_reexecuted(self) -> None:
        self.assertIn("if attempt_row.status = 'completed' then", self.sql)
        self.assertIn("return query select false, attempt_row.status, attempt_row.order_payload", self.sql)
        self.assertIn("return query select false, attempt_row.status, null::jsonb", self.sql)

    def test_attempt_begin_revalidates_task_analysis_and_lease(self) -> None:
        self.assertIn("task_row.approved_expectation_version <> input_expectation_version", self.sql)
        self.assertIn("analysis_count <> 1 or canonical_analysis_id <> input_analysis_id", self.sql)
        self.assertIn("claim_row.task_id <> input_task_id", self.sql)
        self.assertIn("claim_row.analysis_id <> input_analysis_id", self.sql)
        self.assertIn("claim_row.claim_token <> input_claim_token", self.sql)
        self.assertIn("claim_row.lease_expires_at <= now_value", self.sql)
        self.assertIn("make_interval(secs => greatest(input_lease_seconds, 1))", self.sql)


if __name__ == "__main__":
    unittest.main()
