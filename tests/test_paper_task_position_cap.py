from __future__ import annotations

import unittest

from trading_system.models import (
    ComponentAssessment,
    Direction,
    PortfolioState,
    StrategyInputs,
    TradeLevels,
    TradingMode,
)
from trading_system.pipeline import PaperTradingPipeline
from trading_system.risk import RiskConfig, RiskEngine
from trading_system.tracked_event_paper_bridge import (
    CanonicalTradingTaskExecutionContext,
    _pipeline_with_task_cap,
)


def _component(name: str, direction: Direction, score: int, maximum: int) -> ComponentAssessment:
    return ComponentAssessment(name, direction, score, maximum, (f"{name} evidence",))


def _inputs() -> StrategyInputs:
    return StrategyInputs(
        instrument="DAKT",
        fundamental=_component("fundamental", Direction.SHORT, 30, 35),
        catalyst=_component("catalyst", Direction.SHORT, 21, 25),
        technical=_component("technical", Direction.SHORT, 16, 20),
        market_memory=_component("market_memory", Direction.SHORT, 9, 10),
        news_sentiment=_component("news_sentiment", Direction.SHORT, 6, 10),
        source_event_id="calendar:dakt-q1",
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity=100_000,
        cash=100_000,
        open_positions=0,
        spread_pct=0.2,
        volatility_pct=3.0,
    )


LEVELS = TradeLevels(entry=100.0, stop=105.0, target_1=90.0, target_2=85.0)


class PaperTaskPositionCapTests(unittest.TestCase):
    def test_risk_engine_never_sizes_above_explicit_position_cap(self) -> None:
        pipeline = PaperTradingPipeline(
            risk_engine=RiskEngine(RiskConfig(max_position_value_usd=500.0)),
        )

        result = pipeline.run(_inputs(), LEVELS, _portfolio())

        self.assertEqual(result.proposal.risk.max_position_value, 500.0)
        self.assertEqual(result.proposal.risk.max_quantity, 5)
        self.assertEqual(result.proposal.risk.max_fractional_notional_usd, 500.0)
        self.assertIsNotNone(result.order)

    def test_execution_context_applies_task_cap_to_existing_pipeline(self) -> None:
        base = PaperTradingPipeline()
        task = CanonicalTradingTaskExecutionContext(
            task_id="task-1",
            source_event_id="calendar:dakt-q1",
            instrument="DAKT",
            mode=TradingMode.PAPER,
            max_position_value_usd=500.0,
        )

        capped = _pipeline_with_task_cap(base, task)
        assert capped is not None
        result = capped.run(_inputs(), LEVELS, _portfolio())

        self.assertEqual(result.proposal.risk.max_position_value, 500.0)
        self.assertEqual(result.proposal.risk.max_fractional_notional_usd, 500.0)
        self.assertIsNotNone(result.order)

    def test_task_cap_cannot_relax_a_stricter_existing_risk_cap(self) -> None:
        base = PaperTradingPipeline(
            risk_engine=RiskEngine(RiskConfig(max_position_value_usd=250.0)),
        )
        task = CanonicalTradingTaskExecutionContext(
            task_id="task-1",
            source_event_id="calendar:dakt-q1",
            instrument="DAKT",
            mode=TradingMode.PAPER,
            max_position_value_usd=500.0,
        )

        capped = _pipeline_with_task_cap(base, task)
        assert capped is not None
        result = capped.run(_inputs(), LEVELS, _portfolio())

        self.assertEqual(result.proposal.risk.max_position_value, 250.0)

    def test_position_cap_must_be_finite_and_positive(self) -> None:
        for invalid in (0.0, -1.0, float("inf"), float("nan")):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    RiskEngine(RiskConfig(max_position_value_usd=invalid))


if __name__ == "__main__":
    unittest.main()
