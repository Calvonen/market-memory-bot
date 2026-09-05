from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from trading_system.models import TradingMode
from trading_system.tracked_event_paper_bridge import (
    CanonicalTradingTaskExecutionContext,
    run_post_release_paper_from_tracked_event,
)


class EarningsObservationLifecycleTests(unittest.TestCase):
    def test_incomplete_observation_window_returns_explicit_lifecycle_state(self) -> None:
        event = SimpleNamespace(
            calendar_event_id=None,
            event_id="tracked-1",
            instrument="EXM.ASX",
            kind="earnings",
        )
        expectation = SimpleNamespace(
            event_id="tracked:tracked-1",
            instrument="EXM.ASX",
        )
        task = CanonicalTradingTaskExecutionContext(
            task_id="task-1",
            source_event_id="tracked:tracked-1",
            instrument="EXM.ASX",
            mode=TradingMode.PAPER,
        )

        with (
            patch(
                "trading_system.tracked_event_paper_bridge._observation_window_complete",
                return_value=False,
            ),
            patch(
                "trading_system.tracked_event_paper_bridge.run_post_release_paper"
            ) as run_paper,
        ):
            result = run_post_release_paper_from_tracked_event(
                event=event,
                expectation=expectation,
                analysis=Mock(),
                reactions=(),
                portfolio=Mock(),
                resolver=Mock(),
                trading_task=task,
            )

        self.assertEqual(result.status, "observing_post_release")
        self.assertIn("first 30 minutes", result.message)
        run_paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
