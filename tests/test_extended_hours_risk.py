from __future__ import annotations

import unittest

from trading_system.models import Direction, PortfolioState, RiskStatus, TradeCandidate
from trading_system.risk import RiskConfig, RiskEngine


class ExtendedHoursRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = TradeCandidate(
            instrument="TEST",
            direction=Direction.LONG,
            confidence=80,
            entry=100.0,
            stop=95.0,
            target_1=110.0,
        )
        self.portfolio = PortfolioState(
            equity=10_000.0,
            cash=10_000.0,
            open_positions=0,
            spread_pct=0.2,
            volatility_pct=3.0,
        )

    def _configured_engine(self) -> RiskEngine:
        return RiskEngine(
            RiskConfig(
                extended_hours_max_spread_pct=0.5,
                extended_hours_max_volatility_pct=6.0,
                extended_hours_position_size_multiplier=0.5,
            )
        )

    def test_normal_session_behavior_is_unchanged_without_extended_policy(self) -> None:
        proposal = RiskEngine().evaluate(self.candidate, self.portfolio)

        self.assertEqual(proposal.risk.status, RiskStatus.PASS)
        self.assertEqual(proposal.risk.max_risk_amount, 50.0)
        self.assertEqual(proposal.risk.max_position_value, 2_000.0)
        self.assertEqual(proposal.risk.max_quantity, 10)

    def test_extended_hours_fail_closed_without_explicit_risk_policy(self) -> None:
        proposal = RiskEngine().evaluate(
            self.candidate,
            self.portfolio,
            uses_extended_hours=True,
        )

        self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
        self.assertIn("extended_hours_risk_policy_missing", proposal.risk.reasons)
        self.assertEqual(proposal.risk.max_quantity, 0)

    def test_configured_extended_hours_reduce_risk_and_position_size(self) -> None:
        proposal = self._configured_engine().evaluate(
            self.candidate,
            self.portfolio,
            uses_extended_hours=True,
        )

        self.assertEqual(proposal.risk.status, RiskStatus.PASS)
        self.assertEqual(proposal.risk.max_risk_amount, 25.0)
        self.assertEqual(proposal.risk.max_position_value, 1_000.0)
        self.assertEqual(proposal.risk.max_quantity, 5)

    def test_extended_hours_multiplier_applies_after_cash_cap(self) -> None:
        portfolio = PortfolioState(
            equity=10_000.0,
            cash=500.0,
            open_positions=0,
            spread_pct=0.2,
            volatility_pct=3.0,
        )

        proposal = self._configured_engine().evaluate(
            self.candidate,
            portfolio,
            uses_extended_hours=True,
        )

        self.assertEqual(proposal.risk.status, RiskStatus.PASS)
        self.assertEqual(proposal.risk.max_position_value, 250.0)
        self.assertEqual(proposal.risk.max_quantity, 2)

    def test_extended_hours_multiplier_applies_after_absolute_position_cap(self) -> None:
        engine = RiskEngine(
            RiskConfig(
                max_position_value_usd=600.0,
                extended_hours_max_spread_pct=0.5,
                extended_hours_max_volatility_pct=6.0,
                extended_hours_position_size_multiplier=0.5,
            )
        )

        proposal = engine.evaluate(
            self.candidate,
            self.portfolio,
            uses_extended_hours=True,
        )

        self.assertEqual(proposal.risk.status, RiskStatus.PASS)
        self.assertEqual(proposal.risk.max_position_value, 300.0)
        self.assertEqual(proposal.risk.max_quantity, 3)

    def test_configured_extended_hours_enforce_tighter_spread_and_volatility(self) -> None:
        cases = (
            (0.6, 3.0, "extended_hours_spread_too_wide"),
            (0.2, 7.0, "extended_hours_volatility_too_high"),
        )
        for spread_pct, volatility_pct, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                portfolio = PortfolioState(
                    equity=10_000.0,
                    cash=10_000.0,
                    open_positions=0,
                    spread_pct=spread_pct,
                    volatility_pct=volatility_pct,
                )
                proposal = self._configured_engine().evaluate(
                    self.candidate,
                    portfolio,
                    uses_extended_hours=True,
                )

                self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
                self.assertIn(expected_reason, proposal.risk.reasons)
                self.assertEqual(proposal.risk.max_quantity, 0)

    def test_extended_hours_policy_cannot_weaken_normal_limits(self) -> None:
        invalid_configs = (
            RiskConfig(extended_hours_max_spread_pct=1.1),
            RiskConfig(extended_hours_max_volatility_pct=13.0),
            RiskConfig(extended_hours_position_size_multiplier=1.1),
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    RiskEngine(config)


if __name__ == "__main__":
    unittest.main()
