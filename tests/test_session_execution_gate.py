from __future__ import annotations

import unittest

from trading_system.brokers.base import Broker, BrokerOrder
from trading_system.models import TradeProposal
from trading_system.session_execution_gate import evaluate_session_execution
from trading_system.trading_session_state import TradingSessionState


class _Broker(Broker):
    def execute(self, proposal: TradeProposal) -> BrokerOrder:  # pragma: no cover
        raise NotImplementedError


class _ExtendedBroker(_Broker):
    supports_extended_hours_orders = True


class _WrapperWithoutCapability:
    pass


class SessionExecutionGateTests(unittest.TestCase):
    def test_regular_session_does_not_require_extended_order_capability(self) -> None:
        decision = evaluate_session_execution(
            session=TradingSessionState(True, False, False, True),
            broker=_Broker(),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "regular_session")

    def test_unobservable_session_fails_closed(self) -> None:
        decision = evaluate_session_execution(
            session=TradingSessionState(False, True, True, False),
            broker=_ExtendedBroker(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "session_not_observable")

    def test_extended_session_requires_broker_order_capability(self) -> None:
        session = TradingSessionState(False, True, True, True)

        unsupported = evaluate_session_execution(session=session, broker=_Broker())
        self.assertFalse(unsupported.allowed)
        self.assertEqual(unsupported.reason, "extended_hours_order_unsupported")

        supported = evaluate_session_execution(session=session, broker=_ExtendedBroker())
        self.assertTrue(supported.allowed)
        self.assertEqual(supported.reason, "extended_hours")

    def test_missing_capability_attribute_fails_closed_instead_of_raising(self) -> None:
        decision = evaluate_session_execution(
            session=TradingSessionState(False, True, True, True),
            broker=_WrapperWithoutCapability(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "extended_hours_order_unsupported")


if __name__ == "__main__":
    unittest.main()
