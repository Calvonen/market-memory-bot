from __future__ import annotations

import pathlib
import unittest

from trading_system.brokers.base import BrokerOrder, broker_order_payload
from trading_system.models import Direction


ROOT = pathlib.Path(__file__).resolve().parents[1]
PAPER_REPOSITORY = ROOT / "trading_system" / "paper_trade_repository.py"


class EtoroDemoTerminalOrderPersistenceTests(unittest.TestCase):
    def test_canonical_order_payload_preserves_reconciled_etoro_fields(self) -> None:
        payload = broker_order_payload(
            BrokerOrder(
                order_id="order-1",
                instrument="BTC",
                direction=Direction.LONG,
                quantity=1,
                reference_price=76000.0,
                status="ETORO_DEMO_FILLED",
                notional_usd=125.0,
                broker_position_id="position-1",
            )
        )
        self.assertEqual(payload["notional_usd"], 125.0)
        self.assertEqual(payload["broker_position_id"], "position-1")
        self.assertEqual(payload["status"], "ETORO_DEMO_FILLED")

    def test_terminal_paper_run_uses_canonical_broker_order_serializer(self) -> None:
        source = PAPER_REPOSITORY.read_text(encoding="utf-8")
        self.assertIn(
            'payload["paper_order"] = broker_order_payload(result.pipeline.order)',
            source,
        )
        self.assertIn(
            "from trading_system.brokers.base import broker_order_payload",
            source,
        )


if __name__ == "__main__":
    unittest.main()
