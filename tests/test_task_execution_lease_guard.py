from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from trading_system.brokers.base import BrokerOrder
from trading_system.models import (
    Direction,
    RiskDecision,
    RiskStatus,
    ScoreBreakdown,
    StrategyDecision,
    TradeCandidate,
    TradeProposal,
)
from trading_system.tracked_event_paper_orchestration import _LeaseGuardedBroker


MIGRATION = Path("supabase/migrations/20260903154000_task_execution_lease_guard.sql")


class _Broker:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, proposal):
        self.calls += 1
        return BrokerOrder(
            order_id="order-1",
            instrument="EXM.ASX",
            direction=Direction.LONG,
            quantity=1,
            reference_price=10.0,
            status="FILLED",
            created_at=datetime(2026, 8, 31, 3, 0, tzinfo=UTC),
        )


def _proposal() -> TradeProposal:
    strategy = StrategyDecision(
        instrument="EXM.ASX",
        direction=Direction.LONG,
        confidence=80,
        scores=ScoreBreakdown(fundamental=2, catalyst=2, technical=2, market_memory=2),
        source_event_id="tracked:event-1",
    )
    candidate = TradeCandidate(
        instrument="EXM.ASX",
        direction=Direction.LONG,
        confidence=80,
        entry=10.0,
        stop=9.5,
        target_1=11.0,
        source_event_id="tracked:event-1",
        strategy_decision_id=strategy.decision_id,
    )
    risk = RiskDecision(
        status=RiskStatus.PASS,
        reasons=(),
        max_risk_amount=10.0,
        max_position_value=10.0,
        max_quantity=1,
        reward_risk=2.0,
    )
    return TradeProposal(candidate=candidate, risk=risk, strategy_decision=strategy)


class TaskExecutionLeaseGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_durable_attempt_runs_immediately_before_broker(self) -> None:
        events: list[str] = []
        broker = _Broker()

        def begin(token, strategy, risk):
            events.append("reserve")
            return {"can_execute": True, "attempt_status": "started", "order_payload": None}

        def complete(token, order):
            events.append("complete")

        guarded = _LeaseGuardedBroker(broker, begin, complete)
        guarded.execute(_proposal())
        self.assertEqual(events, ["reserve", "complete"])
        self.assertEqual(broker.calls, 1)

    def test_failed_reservation_never_calls_broker(self) -> None:
        broker = _Broker()
        guarded = _LeaseGuardedBroker(
            broker,
            lambda token, strategy, risk: {
                "can_execute": False,
                "attempt_status": "started",
                "order_payload": None,
            },
            lambda token, order: self.fail("blocked attempt must not complete"),
        )
        with self.assertRaisesRegex(RuntimeError, "outcome is uncertain"):
            guarded.execute(_proposal())
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
