from __future__ import annotations

import unittest

from trading_system.models import TradingMode
from trading_system.pipeline import PaperTradingPipeline
from trading_system.tracked_event_paper_bridge import (
    CanonicalTradingTaskExecutionContext,
    _pipeline_with_task_cap,
)


class TrackedEventTaskCapExtendedHoursTests(unittest.TestCase):
    def test_task_cap_preserves_extended_hours_risk_context(self) -> None:
        pipeline = PaperTradingPipeline(uses_extended_hours=True)
        task = CanonicalTradingTaskExecutionContext(
            task_id="task-1",
            source_event_id="tracked:event-1",
            instrument="HAS.L",
            mode=TradingMode.PAPER,
            max_position_value_usd=1_000.0,
        )

        capped = _pipeline_with_task_cap(pipeline, task)

        self.assertIsNotNone(capped)
        assert capped is not None
        self.assertTrue(capped.uses_extended_hours)
        self.assertEqual(capped.risk_engine.config.max_position_value_usd, 1_000.0)


if __name__ == "__main__":
    unittest.main()
