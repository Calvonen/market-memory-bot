from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_tracked_event_paper_orchestration import (
    Expectations,
    PaperRuns,
    PORTFOLIO,
    Releases,
    TASK_ID,
    TRACKED_ID,
    TrackedEvents,
    TradingTasks,
    task,
)
from trading_system.post_release_paper import PostReleasePaperResult
from trading_system.tracked_event_paper_orchestration import run_approved_tracked_paper_once
from trading_system.trading_session_state import TradingSessionState


class EarningsConfirmationSessionWiringTests(unittest.TestCase):
    def _run(
        self,
        *,
        paper: PaperRuns,
        session: TradingSessionState | None = None,
        session_reader=None,
    ):
        return run_approved_tracked_paper_once(
            tracked_event_id=TRACKED_ID,
            task_id=TASK_ID,
            tracked_events=TrackedEvents(),
            expectations=Expectations(),
            releases=Releases(),
            trading_tasks=TradingTasks(task()),
            paper_runs=paper,
            resolver=SimpleNamespace(),
            portfolio=PORTFOLIO,
            session=session,
            session_reader=session_reader,
        )

    def test_unobservable_session_waits_before_bridge_or_persistence(self) -> None:
        paper = PaperRuns()
        stale_session = TradingSessionState(
            exchange_session_open=True,
            broker_extended_session_available=True,
            allow_extended_hours=True,
            market_data_fresh=False,
        )

        with patch(
            "trading_system.tracked_event_paper_orchestration.run_post_release_paper_from_tracked_event"
        ) as bridge:
            result = self._run(paper=paper, session=stale_session)

        self.assertEqual(result.status, "waiting_confirmation")
        self.assertIn("market_data_stale", result.message)
        self.assertEqual(len(paper.claim_calls), 1)
        self.assertEqual(paper.save_calls, [])
        bridge.assert_not_called()

    def test_session_reader_unavailability_waits_before_bridge_or_persistence(self) -> None:
        paper = PaperRuns()
        session_reader = Mock(
            side_effect=RuntimeError("timed out waiting for explicit eToro session evidence")
        )

        with patch(
            "trading_system.tracked_event_paper_orchestration.run_post_release_paper_from_tracked_event"
        ) as bridge:
            result = self._run(paper=paper, session_reader=session_reader)

        self.assertEqual(result.status, "waiting_confirmation")
        self.assertIn("timed out waiting for explicit eToro session evidence", result.message)
        self.assertEqual(len(paper.claim_calls), 1)
        self.assertEqual(paper.save_calls, [])
        session_reader.assert_called_once_with()
        bridge.assert_not_called()

    def test_fresh_broker_session_continues_confirmation_without_order_authority(self) -> None:
        paper = PaperRuns()
        broker_session = TradingSessionState(
            exchange_session_open=False,
            broker_extended_session_available=True,
            allow_extended_hours=False,
            market_data_fresh=True,
        )
        bridge_result = PostReleasePaperResult(
            "waiting_confirmation",
            "confirmation still pending",
        )

        with patch(
            "trading_system.tracked_event_paper_orchestration.run_post_release_paper_from_tracked_event",
            return_value=bridge_result,
        ) as bridge:
            result = self._run(paper=paper, session=broker_session)

        self.assertEqual(result.status, "waiting_confirmation")
        bridge.assert_called_once()
        self.assertEqual(len(paper.save_calls), 1)
        self.assertIs(paper.save_calls[0]["result"], bridge_result)


if __name__ == "__main__":
    unittest.main()
