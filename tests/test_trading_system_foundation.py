import unittest
from datetime import UTC, datetime, timedelta

from trading_system.brokers.paper import PaperBroker
from trading_system.models import Direction, PortfolioState, RiskStatus, TradeCandidate, TradingMode
from trading_system.risk import RiskConfig, RiskEngine


class TradingSystemFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RiskEngine()
        self.portfolio = PortfolioState(
            equity=10_000.0,
            cash=10_000.0,
            open_positions=0,
            spread_pct=0.2,
            volatility_pct=3.0,
        )
        self.short_candidate = TradeCandidate(
            instrument="HAS.L",
            direction=Direction.SHORT,
            confidence=82,
            entry=100.0,
            stop=105.0,
            target_1=90.0,
            target_2=85.0,
            rationale=("event setup", "negative catalyst"),
            source_event_id="hays-fy2026-results",
        )

    def test_valid_paper_trade_passes_and_sizes_from_risk(self) -> None:
        proposal = self.engine.evaluate(self.short_candidate, self.portfolio)

        self.assertEqual(proposal.mode, TradingMode.PAPER)
        self.assertEqual(proposal.risk.status, RiskStatus.PASS)
        self.assertEqual(proposal.risk.max_risk_amount, 50.0)
        self.assertEqual(proposal.risk.max_position_value, 2_000.0)
        self.assertEqual(proposal.risk.max_quantity, 10)
        self.assertEqual(proposal.risk.reward_risk, 2.0)

    def test_decimal_prices_do_not_undersize_by_one_unit(self) -> None:
        candidate = TradeCandidate(
            instrument="HAS.L",
            direction=Direction.LONG,
            confidence=63,
            entry=0.80,
            stop=0.76,
            target_1=0.88,
        )

        proposal = self.engine.evaluate(candidate, self.portfolio)

        self.assertEqual(proposal.risk.status, RiskStatus.PASS)
        self.assertEqual(proposal.risk.max_quantity, 1250)
        self.assertEqual(proposal.risk.reward_risk, 2.0)

    def test_live_trading_is_disabled_by_default(self) -> None:
        proposal = self.engine.evaluate(
            self.short_candidate,
            self.portfolio,
            requested_mode=TradingMode.LIVE,
        )

        self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
        self.assertIn("live_trading_disabled", proposal.risk.reasons)

    def test_no_trade_cannot_pass_risk_engine(self) -> None:
        candidate = TradeCandidate(
            instrument="HAS.L",
            direction=Direction.NO_TRADE,
            confidence=25,
            entry=None,
            stop=None,
            target_1=None,
        )
        proposal = self.engine.evaluate(candidate, self.portfolio)

        self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
        self.assertEqual(proposal.risk.reasons, ("strategy_returned_no_trade",))
        self.assertEqual(proposal.risk.max_quantity, 0)
        self.assertIsNone(proposal.risk.reward_risk)

    def test_reward_risk_below_minimum_is_rejected(self) -> None:
        candidate = TradeCandidate(
            instrument="HAS.L",
            direction=Direction.LONG,
            confidence=70,
            entry=100.0,
            stop=95.0,
            target_1=105.0,
        )
        proposal = self.engine.evaluate(candidate, self.portfolio)

        self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
        self.assertIn("reward_risk_below_minimum", proposal.risk.reasons)

    def test_daily_loss_and_cooldown_are_hard_rejections(self) -> None:
        now = datetime.now(UTC)
        portfolio = PortfolioState(
            equity=10_000.0,
            cash=9_700.0,
            open_positions=0,
            daily_pnl=-250.0,
            last_loss_at=now - timedelta(minutes=30),
        )
        proposal = self.engine.evaluate(self.short_candidate, portfolio, now=now)

        self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
        self.assertIn("max_daily_loss_reached", proposal.risk.reasons)
        self.assertIn("loss_cooldown_active", proposal.risk.reasons)

    def test_kill_switch_rejects_every_trade(self) -> None:
        engine = RiskEngine(RiskConfig(kill_switch=True))
        proposal = engine.evaluate(self.short_candidate, self.portfolio)

        self.assertEqual(proposal.risk.status, RiskStatus.REJECT)
        self.assertIn("kill_switch_active", proposal.risk.reasons)

    def test_paper_broker_executes_only_approved_paper_proposal(self) -> None:
        proposal = self.engine.evaluate(self.short_candidate, self.portfolio)
        broker = PaperBroker()

        order = broker.execute(proposal)

        self.assertEqual(order.instrument, "HAS.L")
        self.assertEqual(order.direction, Direction.SHORT)
        self.assertEqual(order.quantity, 10)
        self.assertEqual(order.status, "FILLED_SIMULATED")
        self.assertEqual(len(broker.orders), 1)

    def test_paper_broker_rejects_live_proposal(self) -> None:
        engine = RiskEngine(RiskConfig(live_trading_enabled=True))
        proposal = engine.evaluate(
            self.short_candidate,
            self.portfolio,
            requested_mode=TradingMode.LIVE,
        )
        broker = PaperBroker()

        with self.assertRaises(ValueError):
            broker.execute(proposal)


if __name__ == "__main__":
    unittest.main()
