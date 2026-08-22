import unittest
from datetime import datetime, timezone
from decimal import Decimal

from trading_system.market_reaction import MarketReactionEngine, ReactionDirection
from trading_system.market_reaction_evolution import ReactionEvolution
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_market_reaction_pipeline import TrackedMarketReactionPipeline


def _candle(
    start_minute: int,
    close: str,
    *,
    tracked_id: str = "tracked-1",
    instrument: str = "TEST",
    market: str = "TEST",
    etoro_id: int = 123,
    interval: int = 5,
    source_minutes: int = 5,
) -> TrackedMarketCandle:
    price = Decimal(close)
    return TrackedMarketCandle(
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        interval_minutes=interval,
        start=datetime(2026, 8, 24, 7, start_minute, tzinfo=timezone.utc),
        open=price,
        high=price,
        low=price,
        close=price,
        source_minutes=source_minutes,
    )


class TrackedMarketReactionPipelineTests(unittest.TestCase):
    def test_first_candle_returns_reaction_and_initial_evolution(self) -> None:
        pipeline = TrackedMarketReactionPipeline()

        result = pipeline.add(_candle(0, "98"), reference_price=Decimal("100"))

        self.assertEqual(result.reaction.return_pct, Decimal("-2.00"))
        self.assertEqual(result.reaction.direction, ReactionDirection.NEGATIVE)
        self.assertEqual(result.evolution.evolution, ReactionEvolution.INITIAL)
        self.assertIs(result.evolution.snapshot, result.reaction)

    def test_sequential_candles_flow_through_evolution_tracker(self) -> None:
        pipeline = TrackedMarketReactionPipeline()
        pipeline.add(_candle(0, "98"), reference_price=Decimal("100"))

        extending = pipeline.add(_candle(5, "96"), reference_price=Decimal("100"))
        retracing = pipeline.add(_candle(10, "97"), reference_price=Decimal("100"))
        reversal = pipeline.add(_candle(15, "102"), reference_price=Decimal("100"))

        self.assertEqual(extending.evolution.evolution, ReactionEvolution.EXTENDING)
        self.assertEqual(retracing.evolution.evolution, ReactionEvolution.RETRACING)
        self.assertEqual(reversal.evolution.evolution, ReactionEvolution.REVERSAL)
        self.assertEqual(reversal.evolution.delta_pct_points, Decimal("5.00"))

    def test_reference_price_change_fails_closed_without_corrupting_state(self) -> None:
        pipeline = TrackedMarketReactionPipeline()
        pipeline.add(_candle(0, "98"), reference_price=Decimal("100"))

        with self.assertRaises(ValueError):
            pipeline.add(_candle(5, "97"), reference_price=Decimal("101"))

        result = pipeline.add(_candle(5, "97"), reference_price=Decimal("100"))
        self.assertEqual(result.evolution.evolution, ReactionEvolution.RETRACING)
        self.assertEqual(result.evolution.previous_return_pct, Decimal("-2.00"))

    def test_state_is_isolated_by_interval(self) -> None:
        pipeline = TrackedMarketReactionPipeline()

        five = pipeline.add(_candle(0, "98", interval=5, source_minutes=5), reference_price=Decimal("100"))
        fifteen = pipeline.add(
            _candle(0, "97", interval=15, source_minutes=15),
            reference_price=Decimal("100"),
        )

        self.assertEqual(five.evolution.evolution, ReactionEvolution.INITIAL)
        self.assertEqual(fifteen.evolution.evolution, ReactionEvolution.INITIAL)

    def test_custom_engine_threshold_is_respected(self) -> None:
        pipeline = TrackedMarketReactionPipeline(
            engine=MarketReactionEngine(flat_threshold_pct=Decimal("1.0"))
        )

        result = pipeline.add(_candle(0, "100.5"), reference_price=Decimal("100"))

        self.assertEqual(result.reaction.direction, ReactionDirection.FLAT)
        self.assertEqual(result.evolution.evolution, ReactionEvolution.INITIAL)

    def test_invalid_candle_fails_before_sequence_state_is_created(self) -> None:
        pipeline = TrackedMarketReactionPipeline()

        with self.assertRaises(ValueError):
            pipeline.add(_candle(0, "0"), reference_price=Decimal("100"))

        result = pipeline.add(_candle(0, "98"), reference_price=Decimal("100"))
        self.assertEqual(result.evolution.evolution, ReactionEvolution.INITIAL)


if __name__ == "__main__":
    unittest.main()
