from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from trading_system.brokers.base import BrokerOrder
from trading_system.models import Direction
from trading_system.tracked_event_paper_orchestration import _LeaseGuardedBroker
from trading_system.trading_session_state import TradingSessionState


ORDER = BrokerOrder(
    order_id="order-1",
    instrument="EXM.ASX",
    direction=Direction.LONG,
    quantity=1,
    reference_price=10.0,
    status="FILLED",
)


class RecordingBroker:
    supports_extended_hours_orders = False

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _proposal):
        self.calls += 1
        return ORDER


class PaperSessionExecutionOrchestrationGateTests(unittest.TestCase):
    def test_session_reader_runs_at_broker_attempt_boundary(self) -> None:
        broker = RecordingBroker()
        calls: list[str] = []
        guarded = _LeaseGuardedBroker(
            broker,
            lambda *_args: calls.append("begin") or {"can_execute": True},
            lambda *_args: None,
            session_reader=lambda: calls.append("session")
            or TradingSessionState(True, False, False, True),
        )

        with (
            patch(
                "trading_system.tracked_event_paper_orchestration._strategy_payload",
                return_value={"strategy": "audit"},
            ),
            patch(
                "trading_system.tracked_event_paper_orchestration._risk_payload",
                return_value={"risk": "audit"},
            ),
        ):
            guarded.execute(SimpleNamespace())

        self.assertEqual(calls, ["session", "begin"])
        self.assertEqual(broker.calls, 1)

    def test_session_reader_failure_blocks_before_broker_attempt(self) -> None:
        broker = RecordingBroker()
        begins: list[object] = []
        guarded = _LeaseGuardedBroker(
            broker,
            lambda *args: begins.append(args) or {"can_execute": True},
            lambda *_args: None,
            session_reader=lambda: (_ for _ in ()).throw(RuntimeError("stale evidence")),
        )

        with self.assertRaisesRegex(RuntimeError, "stale evidence"):
            guarded.execute(SimpleNamespace())

        self.assertEqual(begins, [])
        self.assertEqual(broker.calls, 0)

    def test_unobservable_session_blocks_before_broker_attempt(self) -> None:
        broker = RecordingBroker()
        begins: list[object] = []
        guarded = _LeaseGuardedBroker(
            broker,
            lambda *args: begins.append(args) or {"can_execute": True},
            lambda *_args: None,
            session=TradingSessionState(False, True, True, False),
        )

        with self.assertRaisesRegex(RuntimeError, "session_not_observable"):
            guarded.execute(SimpleNamespace())

        self.assertEqual(begins, [])
        self.assertEqual(broker.calls, 0)

    def test_unsupported_extended_session_blocks_before_broker_attempt(self) -> None:
        broker = RecordingBroker()
        begins: list[object] = []
        guarded = _LeaseGuardedBroker(
            broker,
            lambda *args: begins.append(args) or {"can_execute": True},
            lambda *_args: None,
            session=TradingSessionState(False, True, True, True),
        )

        with self.assertRaisesRegex(RuntimeError, "extended_hours_order_unsupported"):
            guarded.execute(SimpleNamespace())

        self.assertEqual(begins, [])
        self.assertEqual(broker.calls, 0)

    def test_regular_session_preserves_existing_execution_path(self) -> None:
        broker = RecordingBroker()
        begins: list[object] = []
        completions: list[object] = []
        guarded = _LeaseGuardedBroker(
            broker,
            lambda *args: begins.append(args) or {"can_execute": True},
            lambda *args: completions.append(args),
            session=TradingSessionState(True, False, False, True),
        )

        with (
            patch(
                "trading_system.tracked_event_paper_orchestration._strategy_payload",
                return_value={"strategy": "audit"},
            ),
            patch(
                "trading_system.tracked_event_paper_orchestration._risk_payload",
                return_value={"risk": "audit"},
            ),
        ):
            result = guarded.execute(SimpleNamespace())

        self.assertIs(result, ORDER)
        self.assertEqual(len(begins), 1)
        self.assertEqual(len(completions), 1)
        self.assertEqual(broker.calls, 1)

    def test_missing_session_preserves_existing_execution_path(self) -> None:
        broker = RecordingBroker()
        guarded = _LeaseGuardedBroker(
            broker,
            lambda *_args: {"can_execute": True},
            lambda *_args: None,
        )

        with (
            patch(
                "trading_system.tracked_event_paper_orchestration._strategy_payload",
                return_value={"strategy": "audit"},
            ),
            patch(
                "trading_system.tracked_event_paper_orchestration._risk_payload",
                return_value={"risk": "audit"},
            ),
        ):
            result = guarded.execute(SimpleNamespace())

        self.assertIs(result, ORDER)
        self.assertEqual(broker.calls, 1)


if __name__ == "__main__":
    unittest.main()
