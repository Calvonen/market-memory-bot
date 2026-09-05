import unittest

from trading_system.models import (
    ComponentAssessment,
    Direction,
    PortfolioState,
    StrategyInputs,
    TradeLevels,
)
from trading_system.pipeline import InMemoryDecisionJournal, PaperTradingPipeline
from trading_system.strategy import StrategyEngine


def component(name: str, direction: Direction, score: int, max_score: int) -> ComponentAssessment:
    return ComponentAssessment(name, direction, score, max_score, (f"{name} evidence",))


class StrategyPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.short_inputs = StrategyInputs(
            instrument="HAS.L",
            fundamental=component("fundamental", Direction.SHORT, 30, 35),
            catalyst=component("catalyst", Direction.SHORT, 21, 25),
            technical=component("technical", Direction.SHORT, 16, 20),
            market_memory=component("market_memory", Direction.SHORT, 9, 10),
            news_sentiment=component("news_sentiment", Direction.SHORT, 6, 10),
            source_event_id="hays-fy2026-results",
            invalidation=("guidance recovers above pre-event base case",),
        )
        self.portfolio = PortfolioState(
            equity=10_000,
            cash=10_000,
            open_positions=0,
            spread_pct=0.2,
            volatility_pct=3.0,
        )
        self.levels = TradeLevels(entry=100.0, stop=105.0, target_1=90.0, target_2=85.0)

    def test_example_hays_score_produces_short_82(self) -> None:
        decision = StrategyEngine().evaluate(self.short_inputs)

        self.assertEqual(decision.direction, Direction.SHORT)
        self.assertEqual(decision.confidence, 82)
        self.assertEqual(decision.scores.fundamental, 30)
        self.assertEqual(decision.scores.catalyst, 21)
        self.assertEqual(decision.scores.technical, 16)
        self.assertEqual(decision.scores.market_memory, 9)
        self.assertEqual(decision.scores.news_sentiment, 6)
        self.assertEqual(decision.scores.total, 82)

    def test_conflicting_evidence_returns_no_trade(self) -> None:
        inputs = StrategyInputs(
            instrument="HAS.L",
            fundamental=component("fundamental", Direction.SHORT, 25, 35),
            catalyst=component("catalyst", Direction.LONG, 20, 25),
            technical=component("technical", Direction.LONG, 16, 20),
            market_memory=component("market_memory", Direction.SHORT, 8, 10),
            news_sentiment=component("news_sentiment", Direction.NO_TRADE, 0, 10),
        )

        decision = StrategyEngine().evaluate(inputs)

        self.assertEqual(decision.direction, Direction.NO_TRADE)
        self.assertEqual(decision.long_evidence, 36)
        self.assertEqual(decision.short_evidence, 33)

    def test_pipeline_executes_only_after_strategy_and_risk_pass(self) -> None:
        journal = InMemoryDecisionJournal()
        pipeline = PaperTradingPipeline(journal=journal)

        result = pipeline.run(self.short_inputs, self.levels, self.portfolio)

        self.assertEqual(result.strategy.direction, Direction.SHORT)
        self.assertIsNotNone(result.order)
        self.assertEqual(len(journal.strategies), 1)
        self.assertEqual(len(journal.proposals), 1)
        self.assertEqual(len(journal.orders), 1)
        self.assertEqual(journal.proposals[0].candidate.strategy_decision_id, journal.strategies[0].decision_id)

    def test_extended_hours_context_reaches_risk_engine_and_fails_closed(self) -> None:
        journal = InMemoryDecisionJournal()
        pipeline = PaperTradingPipeline(
            journal=journal,
            uses_extended_hours=True,
        )

        result = pipeline.run(self.short_inputs, self.levels, self.portfolio)

        self.assertEqual(result.strategy.direction, Direction.SHORT)
        self.assertIn("extended_hours_risk_policy_missing", result.proposal.risk.reasons)
        self.assertIsNone(result.order)
        self.assertEqual(len(journal.orders), 0)

    def test_no_trade_is_logged_but_never_sent_to_broker(self) -> None:
        weak_inputs = StrategyInputs(
            instrument="HAS.L",
            fundamental=component("fundamental", Direction.SHORT, 20, 35),
            catalyst=component("catalyst", Direction.SHORT, 15, 25),
            technical=component("technical", Direction.NO_TRADE, 0, 20),
            market_memory=component("market_memory", Direction.NO_TRADE, 0, 10),
            news_sentiment=component("news_sentiment", Direction.NO_TRADE, 0, 10),
        )
        journal = InMemoryDecisionJournal()
        pipeline = PaperTradingPipeline(journal=journal)

        result = pipeline.run(weak_inputs, self.levels, self.portfolio)

        self.assertEqual(result.strategy.direction, Direction.NO_TRADE)
        self.assertIsNone(result.order)
        self.assertEqual(len(journal.strategies), 1)
        self.assertEqual(len(journal.proposals), 1)
        self.assertEqual(len(journal.orders), 0)


if __name__ == "__main__":
    unittest.main()
