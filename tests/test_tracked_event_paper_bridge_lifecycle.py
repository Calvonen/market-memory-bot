from __future__ import annotations

import unittest

from tests.test_tracked_event_paper_bridge import (
    FakeResolver,
    analysis,
    event,
    expectation,
    observation_close,
    portfolio,
    trading_task,
)
from trading_system.earnings_paper_lifecycle import EarningsPaperLifecycleStatus
from trading_system.tracked_event_paper_bridge import run_post_release_paper_from_tracked_event


class TrackedEventPaperBridgeLifecycleTests(unittest.TestCase):
    def test_incomplete_observation_returns_canonical_observing_status(self) -> None:
        result = run_post_release_paper_from_tracked_event(
            event=event(),
            expectation=expectation(),
            analysis=analysis(),
            reactions=(),
            portfolio=portfolio(),
            resolver=FakeResolver(),
            trading_task=trading_task(),
        )

        self.assertIs(result.status, EarningsPaperLifecycleStatus.OBSERVING_POST_RELEASE)
        self.assertEqual(result.status, "observing_post_release")

    def test_complete_window_without_anchor_returns_canonical_waiting_status(self) -> None:
        result = run_post_release_paper_from_tracked_event(
            event=event(),
            expectation=expectation(),
            analysis=analysis(),
            reactions=(observation_close(),),
            portfolio=portfolio(),
            resolver=FakeResolver(),
            trading_task=trading_task(),
        )

        self.assertIs(result.status, EarningsPaperLifecycleStatus.WAITING_CONFIRMATION)
        self.assertEqual(result.status, "waiting_confirmation")


if __name__ == "__main__":
    unittest.main()
