from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_system.models import (
    Direction,
    PortfolioState,
    RiskDecision,
    RiskStatus,
    TradeCandidate,
)
from trading_system.risk import RiskEngine


class FractionalRiskSizingTests(unittest.TestCase):
    def test_high_price_instrument_can_pass_with_fractional_notional(self) -> None:
        candidate = TradeCandidate(
            instrument="BTC",
            direction=Direction.LONG,
            confidence=80,
            entry=76000.0,
            stop=75810.0,
            target_1=76380.0,
        )
        portfolio = PortfolioState(
            equity=10_000.0,
            cash=10_000.0,
            open_positions=0,
            spread_pct=0.2,
            volatility_pct=3.0,
        )

        proposal = RiskEngine().evaluate(
            candidate,
            portfolio,
            allow_fractional_sizing=True,
        )

        self.assertEqual(proposal.risk.status, RiskStatus.PASS)
        self.assertEqual(proposal.risk.max_quantity, 0)
        self.assertGreater(proposal.risk.max_fractional_notional_usd, 0.0)
        self.assertLessEqual(
            proposal.risk.max_fractional_notional_usd,
            proposal.risk.max_position_value,
        )
        self.assertAlmostEqual(proposal.risk.max_fractional_notional_usd, 2_000.0, delta=0.01)

    def test_same_candidate_still_rejects_for_integer_only_broker(self) -> None:
        candidate = TradeCandidate(
            instrument="BTC",
            direction=Direction.LONG,
            confidence=80,
            entry=76000.0,
            stop=75810.0,
            target_1=76380.0,
        )
        portfolio = PortfolioState(
            equity=10_000.0,
            cash=10_000.0,
            open_positions=0,
            spread_pct=0.2,
            volatility_pct=3.0,
        )

        proposal = RiskEngine().evaluate(candidate, portfolio)

        self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
        self.assertIn("position_size_below_one_unit", proposal.risk.reasons)
        self.assertEqual(proposal.risk.max_fractional_notional_usd, 0.0)

    def test_fractional_field_does_not_shift_legacy_positional_audit_fields(self) -> None:
        created_at = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
        decision = RiskDecision(
            RiskStatus.PASS,
            (),
            50.0,
            1000.0,
            2,
            2.0,
            "legacy-decision-id",
            created_at,
        )
        self.assertEqual(decision.decision_id, "legacy-decision-id")
        self.assertEqual(decision.created_at, created_at)
        self.assertEqual(decision.max_fractional_notional_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
