from __future__ import annotations

import unittest

from trading_system.models import (
    ComponentAssessment,
    Direction,
    PortfolioState,
    ScoreBreakdown,
    StrategyDecision,
    StrategyInputs,
    TradeLevels,
)
from trading_system.pipeline import PaperTradingPipeline
from trading_system.tracked_event_paper_orchestration import (
    _LeaseGuardedBroker,
    _risk_payload,
    _strategy_payload,
)


class _Strategy:
    def evaluate(self, inputs):
        return StrategyDecision(
            instrument=inputs.instrument,
            direction=Direction.LONG,
            confidence=90,
            scores=ScoreBreakdown(fundamental=20, catalyst=20, technical=20, market_memory=20),
            source_event_id=inputs.source_event_id,
        )


class _Broker:
    def __init__(self):
        self.proposal = None

    def execute(self, proposal):
        self.proposal = proposal
        raise RuntimeError("stop after proposal capture")


class _FractionalBroker(_Broker):
    supports_fractional_sizing = True


class BrokerAttemptDecisionAuditTests(unittest.TestCase):
    def _inputs(self) -> StrategyInputs:
        component = ComponentAssessment("x", Direction.LONG, 10, 10)
        return StrategyInputs(
            instrument="TEST",
            fundamental=component,
            catalyst=component,
            technical=component,
            market_memory=component,
            news_sentiment=ComponentAssessment("news", Direction.NO_TRADE, 0, 10),
            source_event_id="tracked:event",
        )

    def _portfolio(self) -> PortfolioState:
        return PortfolioState(
            equity=10000,
            cash=10000,
            open_positions=0,
            spread_pct=0.1,
            volatility_pct=2.0,
        )

    def test_pipeline_carries_exact_strategy_into_broker_proposal(self) -> None:
        broker = _Broker()
        pipeline = PaperTradingPipeline(strategy_engine=_Strategy(), broker=broker)
        with self.assertRaisesRegex(RuntimeError, "stop after proposal capture"):
            pipeline.run(
                self._inputs(),
                TradeLevels(entry=100, stop=98, target_1=104),
                self._portfolio(),
            )
        self.assertIsNotNone(broker.proposal)
        self.assertIsNotNone(broker.proposal.strategy_decision)
        self.assertEqual(
            broker.proposal.candidate.strategy_decision_id,
            broker.proposal.strategy_decision.decision_id,
        )
        self.assertEqual(
            _strategy_payload(broker.proposal)["decision_id"],
            broker.proposal.strategy_decision.decision_id,
        )
        self.assertEqual(
            _risk_payload(broker.proposal)["decision_id"],
            broker.proposal.risk.decision_id,
        )

    def test_fractional_authorization_is_in_durable_risk_payload(self) -> None:
        broker = _FractionalBroker()
        pipeline = PaperTradingPipeline(strategy_engine=_Strategy(), broker=broker)
        with self.assertRaisesRegex(RuntimeError, "stop after proposal capture"):
            pipeline.run(
                self._inputs(),
                TradeLevels(entry=76000, stop=75810, target_1=76380),
                self._portfolio(),
            )
        self.assertIsNotNone(broker.proposal)
        self.assertGreater(broker.proposal.risk.max_fractional_notional_usd, 0.0)
        self.assertEqual(
            _risk_payload(broker.proposal)["max_fractional_notional_usd"],
            broker.proposal.risk.max_fractional_notional_usd,
        )

    def test_lease_guard_preserves_fractional_broker_capability(self) -> None:
        broker = _FractionalBroker()
        guarded = _LeaseGuardedBroker(
            broker,
            lambda *_args: {"attempt_status": "started", "can_execute": False},
            lambda *_args: None,
        )
        self.assertTrue(guarded.supports_fractional_sizing)
        pipeline = PaperTradingPipeline(broker=guarded)
        self.assertTrue(pipeline.allow_fractional_sizing)


if __name__ == "__main__":
    unittest.main()
