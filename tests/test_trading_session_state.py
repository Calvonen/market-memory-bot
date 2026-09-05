from __future__ import annotations

import unittest

from trading_system.trading_session_state import TradingSessionState


class TradingSessionStateTests(unittest.TestCase):
    def test_exchange_session_requires_fresh_market_data(self) -> None:
        self.assertTrue(
            TradingSessionState(
                exchange_session_open=True,
                broker_extended_session_available=False,
                allow_extended_hours=False,
                market_data_fresh=True,
            ).execution_observable
        )
        self.assertFalse(
            TradingSessionState(
                exchange_session_open=True,
                broker_extended_session_available=True,
                allow_extended_hours=True,
                market_data_fresh=False,
            ).execution_observable
        )

    def test_closed_exchange_requires_broker_capability_policy_and_fresh_data(self) -> None:
        allowed = TradingSessionState(
            exchange_session_open=False,
            broker_extended_session_available=True,
            allow_extended_hours=True,
            market_data_fresh=True,
        )
        self.assertTrue(allowed.execution_observable)
        self.assertTrue(allowed.uses_extended_hours)

        for state in (
            TradingSessionState(False, False, True, True),
            TradingSessionState(False, True, False, True),
            TradingSessionState(False, True, True, False),
        ):
            with self.subTest(state=state):
                self.assertFalse(state.execution_observable)
                self.assertFalse(state.uses_extended_hours)

    def test_broker_capability_does_not_bypass_stale_data(self) -> None:
        state = TradingSessionState(
            exchange_session_open=False,
            broker_extended_session_available=True,
            allow_extended_hours=True,
            market_data_fresh=False,
        )
        self.assertFalse(state.execution_observable)
        self.assertFalse(state.uses_extended_hours)


if __name__ == "__main__":
    unittest.main()
