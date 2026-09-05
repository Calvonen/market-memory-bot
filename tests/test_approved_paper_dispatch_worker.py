from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from trading_system.approved_paper_dispatch_worker import (
    _etoro_earnings_execution_preflight,
    _run_for_event_kind,
    _unreconciled_market_open_attempts,
)
from trading_system.trading_session_state import TradingSessionState


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
        session_reader = Mock()
        result = _run_for_event_kind(
            event_kind="earnings",
            session_reader=session_reader,
            **self._common(),
        )
        self.assertIs(result, earnings.return_value)
        earnings.assert_called_once()
        self.assertIs(earnings.call_args.kwargs["session_reader"], session_reader)
        session_reader.assert_not_called()
        market_open.assert_not_called()

    @patch("trading_system.approved_paper_dispatch_worker.evaluate_session_execution")
    @patch("trading_system.approved_paper_dispatch_worker.read_etoro_session_state")
    def test_etoro_earnings_preflight_reads_session_after_demo_access(self, read_session, evaluate) -> None:
        calls: list[str] = []
        broker = Mock(instrument_id=6804)
        broker.verify_demo_access.side_effect = lambda: calls.append("demo")
        read_session.side_effect = lambda *_args, **_kwargs: (
            calls.append("session")
            or TradingSessionState(True, False, False, True)
        )
        evaluate.side_effect = lambda **_kwargs: (
            calls.append("gate") or SimpleNamespace(allowed=True, reason="regular_session")
        )

        _etoro_earnings_execution_preflight(Mock(), broker)

        self.assertEqual(calls, ["demo", "session", "gate"])

    @patch("trading_system.approved_paper_dispatch_worker.evaluate_session_execution")
    @patch("trading_system.approved_paper_dispatch_worker.read_etoro_session_state")
    def test_etoro_earnings_preflight_fails_closed_after_final_session_gate(self, read_session, evaluate) -> None:
        broker = Mock(instrument_id=6804)
        read_session.return_value = TradingSessionState(False, True, False, True)
        evaluate.return_value = SimpleNamespace(
            allowed=False,
            reason="session_not_observable",
        )

        with self.assertRaisesRegex(RuntimeError, "session_not_observable"):
            _etoro_earnings_execution_preflight(Mock(), broker)

        broker.verify_demo_access.assert_called_once_with()
        read_session.assert_called_once()
        evaluate.assert_called_once()

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

    def test_completed_recovery_discovery_is_independent_of_task_approval(self) -> None:
        paper_runs = Mock()
        paper_runs.client.rpc.return_value.execute.return_value.data = [
            {"event_id": "tracked:event-1", "task_id": "task-cancelled-after-submit"}
        ]

        rows = _unreconciled_market_open_attempts(paper_runs, limit=7)

        self.assertEqual(
            rows,
            ({"event_id": "tracked:event-1", "task_id": "task-cancelled-after-submit"},),
        )
        paper_runs.client.rpc.assert_called_once_with(
            "list_unreconciled_completed_market_open_broker_attempts",
            {"input_limit": 7},
        )

    def test_completed_recovery_discovery_rejects_blank_identity(self) -> None:
        paper_runs = Mock()
        paper_runs.client.rpc.return_value.execute.return_value.data = [
            {"event_id": "tracked:event-1", "task_id": ""}
        ]

        with self.assertRaisesRegex(RuntimeError, "blank identity"):
            _unreconciled_market_open_attempts(paper_runs, limit=1)


if __name__ == "__main__":
    unittest.main()
