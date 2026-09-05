from __future__ import annotations

import unittest

from trading_system.earnings_confirmation_session_bridge import (
    evaluate_earnings_confirmation_session,
)
from trading_system.trading_session_state import TradingSessionState


class EarningsConfirmationSessionBridgeTests(unittest.TestCase):
    def test_fresh_exchange_session_continues_confirmation(self) -> None:
        decision = evaluate_earnings_confirmation_session(
            session=TradingSessionState(True, False, False, True)
        )
        self.assertTrue(decision.continue_confirmation)
        self.assertEqual(decision.reason, "exchange_session")

    def test_fresh_broker_session_can_continue_without_extended_order_permission(self) -> None:
        decision = evaluate_earnings_confirmation_session(
            session=TradingSessionState(False, True, False, True)
        )
        self.assertTrue(decision.continue_confirmation)
        self.assertEqual(decision.reason, "broker_session")

    def test_closed_unavailable_broker_session_waits(self) -> None:
        decision = evaluate_earnings_confirmation_session(
            session=TradingSessionState(False, False, False, True)
        )
        self.assertFalse(decision.continue_confirmation)
        self.assertEqual(decision.reason, "broker_session_unavailable")

    def test_stale_exchange_session_waits(self) -> None:
        decision = evaluate_earnings_confirmation_session(
            session=TradingSessionState(True, False, False, False)
        )
        self.assertFalse(decision.continue_confirmation)
        self.assertEqual(decision.reason, "market_data_stale")

    def test_stale_broker_session_waits(self) -> None:
        decision = evaluate_earnings_confirmation_session(
            session=TradingSessionState(False, True, False, False)
        )
        self.assertFalse(decision.continue_confirmation)
        self.assertEqual(decision.reason, "market_data_stale")


if __name__ == "__main__":
    unittest.main()
