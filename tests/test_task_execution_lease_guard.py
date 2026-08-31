from __future__ import annotations

import unittest
from pathlib import Path

from trading_system.tracked_event_paper_orchestration import _LeaseGuardedBroker


MIGRATION = Path("supabase/migrations/20260903154000_task_execution_lease_guard.sql")


class _Broker:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, proposal):
        self.calls += 1
        return ("order", proposal)


class TaskExecutionLeaseGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_guard_runs_immediately_before_broker(self) -> None:
        events: list[str] = []

        class Broker:
            def execute(self, proposal):
                events.append("broker")
                return proposal

        guarded = _LeaseGuardedBroker(Broker(), lambda: events.append("guard"))
        self.assertEqual(guarded.execute("proposal"), "proposal")
        self.assertEqual(events, ["guard", "broker"])

    def test_failed_guard_never_calls_broker(self) -> None:
        broker = _Broker()

        def reject() -> None:
            raise RuntimeError("lease lost")

        guarded = _LeaseGuardedBroker(broker, reject)
        with self.assertRaisesRegex(RuntimeError, "lease lost"):
            guarded.execute("proposal")
        self.assertEqual(broker.calls, 0)

    def test_approval_and_claim_use_expectation_writer_lock_key(self) -> None:
        self.assertIn("hashtextextended(source_event, 1)", self.sql)
        self.assertIn("hashtextextended(new.event_id, 1)", self.sql)
        self.assertIn("claim_event_paper_run_for_task_v2", self.sql)
        self.assertIn("hashtextextended(input_event_id, 1)", self.sql)
        self.assertIn("claim_event_paper_run_for_task(", self.sql)

    def test_broker_guard_revalidates_exact_authority_and_live_lease(self) -> None:
        self.assertIn("revalidate_event_paper_run_task_lease", self.sql)
        self.assertIn("claim_row.task_id <> input_task_id", self.sql)
        self.assertIn("claim_row.analysis_id <> input_analysis_id", self.sql)
        self.assertIn("claim_row.claim_token <> input_claim_token", self.sql)
        self.assertIn("claim_row.lease_expires_at <= now_value", self.sql)
        self.assertIn("task_row.approved_expectation_version <> input_expectation_version", self.sql)
        self.assertIn("analysis_count <> 1", self.sql)

    def test_active_execution_lease_blocks_task_cancellation(self) -> None:
        self.assertIn("trading_task_execution_lease_active", self.sql)
        self.assertIn("lease_expires_at > clock_timestamp()", self.sql)


if __name__ == "__main__":
    unittest.main()
