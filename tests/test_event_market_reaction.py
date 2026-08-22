import unittest
from datetime import datetime, timezone
from decimal import Decimal

from trading_system.event_market_reaction import (
    EventMarketReactionBaseline,
    EventMarketReactionPipeline,
)
from trading_system.market_reaction_evolution import ReactionEvolution
from trading_system.tracked_candle_pipeline import TrackedMarketCandle


def _candle(
    minute: int,
    close: str,
    *,
    tracked_id: str = "tracked-1",
    instrument: str = "TEST",
    market: str = "TEST",
    etoro_id: int = 123,
    interval: int = 5,
) -> TrackedMarketCandle:
    price = Decimal(close)
    return TrackedMarketCandle(
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        interval_minutes=interval,
        start=datetime(2026, 8, 24, 7, minute, tzinfo=timezone.utc),
        open=price,
        high=price,
        low=price,
        close=price,
        source_minutes=interval,
    )


def _baseline(
    event_id: str,
    reference: str = "100",
    *,
    tracked_id: str = "tracked-1",
    instrument: str = "TEST",
    market: str = "TEST",
    etoro_id: int = 123,
) -> EventMarketReactionBaseline:
    return EventMarketReactionBaseline(
        event_id=event_id,
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        reference_price=Decimal(reference),
    )


class EventMarketReactionPipelineTests(unittest.TestCase):
    def test_first_event_candle_is_initial_and_keeps_event_identity(self) -> None:
        pipeline = EventMarketReactionPipeline()
        baseline = _baseline("event-1")

        result = pipeline.add(_candle(0, "98"), baseline=baseline)

        self.assertEqual(result.event_id, "event-1")
        self.assertIs(result.baseline, baseline)
        self.assertEqual(
            result.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )

    def test_same_event_continues_reaction_sequence(self) -> None:
        pipeline = EventMarketReactionPipeline()
        baseline = _baseline("event-1")
        pipeline.add(_candle(0, "98"), baseline=baseline)

        result = pipeline.add(_candle(5, "96"), baseline=baseline)

        self.assertEqual(
            result.tracked_reaction.evolution.evolution,
            ReactionEvolution.EXTENDING,
        )

    def test_different_events_for_same_instrument_have_independent_state(self) -> None:
        pipeline = EventMarketReactionPipeline()
        pipeline.add(_candle(0, "98"), baseline=_baseline("event-1"))

        second = pipeline.add(_candle(0, "98"), baseline=_baseline("event-2"))

        self.assertEqual(
            second.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )

    def test_baseline_change_fails_closed_without_corrupting_event_state(self) -> None:
        pipeline = EventMarketReactionPipeline()
        baseline = _baseline("event-1", "100")
        pipeline.add(_candle(0, "98"), baseline=baseline)

        with self.assertRaises(ValueError):
            pipeline.add(_candle(5, "97"), baseline=_baseline("event-1", "101"))

        result = pipeline.add(_candle(5, "97"), baseline=baseline)
        self.assertEqual(
            result.tracked_reaction.evolution.evolution,
            ReactionEvolution.EXTENDING,
        )
        self.assertEqual(
            result.tracked_reaction.evolution.previous_return_pct,
            Decimal("-2.00"),
        )

    def test_identity_mismatch_fails_before_event_state_is_created(self) -> None:
        pipeline = EventMarketReactionPipeline()

        with self.assertRaises(ValueError):
            pipeline.add(
                _candle(0, "98"),
                baseline=_baseline("event-1", tracked_id="wrong"),
            )

        result = pipeline.add(_candle(0, "98"), baseline=_baseline("event-1"))
        self.assertEqual(
            result.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )

    def test_invalid_baseline_and_invalid_candle_do_not_create_event_state(self) -> None:
        pipeline = EventMarketReactionPipeline()

        for reference in ("0", "-1", "NaN"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    pipeline.add(
                        _candle(0, "98"),
                        baseline=_baseline("event-1", reference),
                    )

        with self.assertRaises(ValueError):
            pipeline.add(_candle(0, "0"), baseline=_baseline("event-1"))

        result = pipeline.add(_candle(0, "98"), baseline=_baseline("event-1"))
        self.assertEqual(
            result.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )


if __name__ == "__main__":
    unittest.main()
