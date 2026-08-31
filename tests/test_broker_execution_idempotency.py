from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_system.brokers.base import BrokerOrder
from trading_system.models import Direction
from trading_system.tracked_event_paper_orchestration import _LeaseGuardedBroker


class Broker:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, proposal):
        self.calls += 1
        return BrokerOrder(
            order_id="paper-order-1",
            instrument="EXM.ASX",
            direction=Direction.LONG,
            quantity=2,
            reference_price=10.5,
            status="FILLED",
            created_at=datetime(2026, 8, 31, 3, 0, tzinfo=UTC),
        )


class BrokerExecutionIdempotencyTests(unittest.TestCase):
    def test_completed_attempt_reuses_order_without_calling_broker(self) -> None:
        broker = Broker()
        payload = {
            "order_id": "paper-order-1",
            "instrument": "EXM.ASX",
            "direction": "LONG",
            "quantity": 2,
            "reference_price": 10.5,
            "status": "FILLED",
            "created_at": "2026-08-31T03:00:00+00:00",
        }
        guarded = _LeaseGuardedBroker(
            broker,
            lambda token: {
                "can_execute": False,
                "attempt_status": "completed",
                "order_payload": payload,
            },
            lambda token, order: self.fail("completed attempt must not be completed again"),
        )

        order = guarded.execute(object())

        self.assertEqual(order.order_id, "paper-order-1")
        self.assertEqual(broker.calls, 0)

    def test_uncertain_started_attempt_never_calls_broker_again(self) -> None:
        broker = Broker()
        guarded = _LeaseGuardedBroker(
            broker,
            lambda token: {
                "can_execute": False,
                "attempt_status": "started",
                "order_payload": None,
            },
            lambda token, order: self.fail("uncertain attempt must not complete"),
        )

        with self.assertRaisesRegex(RuntimeError, "outcome is uncertain"):
            guarded.execute(object())
        self.assertEqual(broker.calls, 0)

    def test_first_attempt_executes_once_then_records_exact_order(self) -> None:
        broker = Broker()
        completed = []
        guarded = _LeaseGuardedBroker(
            broker,
            lambda token: {
                "can_execute": True,
                "attempt_status": "started",
                "order_payload": None,
            },
            lambda token, order: completed.append((token, order)),
        )

        order = guarded.execute(object())

        self.assertEqual(broker.calls, 1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0][1], order)
        self.assertTrue(completed[0][0])


if __name__ == "__main__":
    unittest.main()
