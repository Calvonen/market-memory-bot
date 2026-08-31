from __future__ import annotations

import unittest

from trading_system.models import Direction, PortfolioState, RiskStatus, TradeCandidate
from trading_system.risk import RiskEngine


class RiskCashLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RiskEngine()
        self.candidate = TradeCandidate(
            instrument="TEST",
            direction=Direction.LONG,
            confidence=80,
            entry=100.0,
            stop=95.0,
            target_1=110.0,
        )

    def test_zero_cash_is_hard_rejection(self) -> None:
        portfolio = PortfolioState(
            equity=10_000.0,
            cash=0.0,
            open_positions=0,
            spread_pct=0.2,
            volatility_pct=3.0,
        )
        proposal = self.engine.evaluate(self.candidate, portfolio)
        self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
        self.assertIn("insufficient_cash", proposal.risk.reasons)
        self.assertEqual(proposal.risk.max_quantity, 0)
        self.assertEqual(proposal.risk.max_position_value, 0.0)

    def test_positive_cash_caps_position_size(self) -> None:
        portfolio = PortfolioState(
            equity=10_000.0,
            cash=350.0,
            open_positions=0,
            spread_pct=0.2,
            volatility_pct=3.0,
        )
        proposal = self.engine.evaluate(self.candidate, portfolio)
        self.assertEqual(proposal.risk.status, RiskStatus.PASS)
        self.assertEqual(proposal.risk.max_position_value, 350.0)
        self.assertEqual(proposal.risk.max_quantity, 3)

    def test_negative_cash_fails_closed(self) -> None:
        portfolio = PortfolioState(
            equity=10_000.0,
            cash=-1.0,
            open_positions=0,
            spread_pct=0.2,
            volatility_pct=3.0,
        )
        proposal = self.engine.evaluate(self.candidate, portfolio)
        self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
        self.assertIn("invalid_portfolio_cash", proposal.risk.reasons)
        self.assertEqual(proposal.risk.max_quantity, 0)


if __name__ == "__main__":
    unittest.main()
