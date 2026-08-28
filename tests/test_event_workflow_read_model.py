from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_system.event_workflow_read_model import build_event_workflow_read_model
from trading_system.event_workflow_readiness import (
    WorkflowExecutionOutcome,
    WorkflowReadinessEvidence,
)
from trading_system.models import TradingMode
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


def _event(*, kind: str = "earnings", status=TrackedEventStatus.MONITORING):
    return PersistentTrackedEvent(
        event_id="tracked-123",
        tracked_instrument_id="instrument-1",
        calendar_event_id=None,
        company_name="Example Plc",
        instrument="EXM",
        market="USA",
        source="manual",
        external_key="example",
        kind=kind,
        title="Example event",
        event_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=status,
    )


class EventWorkflowReadModelTests(unittest.TestCase):
    def test_observation_only_earnings_does_not_invent_trading_steps(self):
        model = build_event_workflow_read_model(
            _event(),
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.MONITORING,
                reaction_present=True,
            ),
        )

        self.assertEqual(model.profile_id, "earnings_documented_observation_v1")
        self.assertIsNone(model.trading_mode)
        self.assertEqual(
            [(step.key, step.mode, step.status) for step in model.steps],
            [
                ("tracking", "required", "running"),
                ("event_identified", "required", "completed"),
                ("release", "required", "pending"),
                ("analysis", "required", "pending"),
                ("market_reaction", "required", "running"),
            ],
        )

    def test_content_event_reports_release_as_skipped(self):
        model = build_event_workflow_read_model(
            _event(kind="news"),
            WorkflowReadinessEvidence(tracked_status=TrackedEventStatus.TRACKED),
        )

        release = next(step for step in model.steps if step.key == "release")
        self.assertEqual(model.profile_id, "content_event_observation_v1")
        self.assertEqual((release.mode, release.status), ("skip", "skipped"))

    def test_explicit_paper_task_adds_strategy_risk_and_paper(self):
        model = build_event_workflow_read_model(
            _event(status=TrackedEventStatus.COMPLETED),
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.COMPLETED,
                release_document_present=True,
                analysis_present=True,
                reaction_present=True,
                strategy_present=True,
                risk_present=True,
                execution_outcome=WorkflowExecutionOutcome.FILLED,
            ),
            trading_mode=TradingMode.PAPER,
        )

        self.assertEqual(model.profile_id, "earnings_documented_paper_v1")
        self.assertEqual(model.trading_mode, "PAPER")
        self.assertEqual(model.steps[-3].key, "strategy")
        self.assertEqual(model.steps[-2].key, "risk")
        self.assertEqual((model.steps[-1].key, model.steps[-1].status), ("paper", "completed"))

    def test_explicit_live_task_is_preserved_without_enabling_it(self):
        model = build_event_workflow_read_model(
            _event(),
            WorkflowReadinessEvidence(tracked_status=TrackedEventStatus.TRACKED),
            trading_mode=TradingMode.LIVE,
        )

        self.assertEqual(model.trading_mode, "LIVE")
        self.assertEqual(model.steps[-1].key, "live")
        self.assertEqual(model.steps[-1].status, "pending")

    def test_unknown_persisted_kind_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unsupported tracked event kind"):
            build_event_workflow_read_model(
                _event(kind="future_kind"),
                WorkflowReadinessEvidence(tracked_status=TrackedEventStatus.TRACKED),
            )

    def test_invalid_trading_mode_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "trading_mode must be"):
            build_event_workflow_read_model(
                _event(),
                WorkflowReadinessEvidence(tracked_status=TrackedEventStatus.TRACKED),
                trading_mode="PAPER",  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
