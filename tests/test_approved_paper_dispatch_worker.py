from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from trading_system.approved_paper_dispatch_worker import _run_for_event_kind


class ApprovedPaperDispatchWorkerTests(unittest.TestCase):
    def _common(self) -> dict:
        return {
            "tracked_event_id": "tracked-1",
            "task_id": "task-1",
            "tracked_events": Mock(),
            "expectations": Mock(),
            "releases": Mock(),
            "trading_tasks": Mock(),
            "paper_runs": Mock(),
            "resolver": Mock(),
            "portfolio": Mock(),
            "lease_seconds": 120,
            "pipeline": Mock(),
        }

    @patch("trading_system.approved_paper_dispatch_worker.run_approved_market_open_paper_once")
    @patch("trading_system.approved_paper_dispatch_worker.run_approved_tracked_paper_once")
    def test_earnings_keeps_existing_orchestration(self, earnings, market_open) -> None:
        earnings.return_value = object()
        result = _run_for_event_kind(event_kind="earnings", **self._common())
        self.assertIs(result, earnings.return_value)
        earnings.assert_called_once()
        market_open.assert_not_called()

    @patch("trading_system.approved_paper_dispatch_worker.run_approved_market_open_paper_once")
    @patch("trading_system.approved_paper_dispatch_worker.run_approved_tracked_paper_once")
    def test_market_open_uses_market_open_orchestration(self, earnings, market_open) -> None:
        market_open.return_value = object()
        result = _run_for_event_kind(event_kind="market_open", **self._common())
        self.assertIs(result, market_open.return_value)
        market_open.assert_called_once()
        earnings.assert_not_called()

    @patch("trading_system.approved_paper_dispatch_worker.run_approved_market_open_paper_once")
    @patch("trading_system.approved_paper_dispatch_worker.run_approved_tracked_paper_once")
    def test_unknown_event_kind_fails_closed(self, earnings, market_open) -> None:
        with self.assertRaisesRegex(ValueError, "not executable"):
            _run_for_event_kind(event_kind="dividend", **self._common())
        earnings.assert_not_called()
        market_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
