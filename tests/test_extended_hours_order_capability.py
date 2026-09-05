from __future__ import annotations

import unittest

from trading_system.brokers.base import Broker, BrokerOrder
from trading_system.brokers.etoro_demo import EtoroDemoBroker
from trading_system.models import TradeProposal


class UnsupportedBroker(Broker):
    def execute(self, proposal: TradeProposal) -> BrokerOrder:  # pragma: no cover - capability only
        raise NotImplementedError


class ExtendedHoursOrderCapabilityTests(unittest.TestCase):
    def test_broker_contract_defaults_extended_hours_orders_to_unsupported(self) -> None:
        self.assertFalse(Broker.supports_extended_hours_orders)
        self.assertFalse(UnsupportedBroker.supports_extended_hours_orders)

    def test_etoro_demo_does_not_claim_extended_hours_order_support_without_verified_contract(self) -> None:
        self.assertFalse(EtoroDemoBroker.supports_extended_hours_orders)


if __name__ == "__main__":
    unittest.main()
