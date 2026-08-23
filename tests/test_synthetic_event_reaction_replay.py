from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.event_reaction_orchestration import EventReactionOrchestrator
from trading_system.market_reaction import ReactionDirection
from trading_system.market_reaction_evolution import ReactionEvolution
from trading_system.tracked_candle_pipeline import TrackedCandlePipeline, TrackedMarketCandle
from trading_system.tracked_etoro_live import TrackedEtoroMarketUpdate
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-synthetic",
    instrument="SYN",
    market="TEST",
    etoro_instrument_id=4242,
    etoro_symbol="SYN",
    etoro_display_name="Synthetic Instrument",
)
EVENT_AT = datetime(2026, 8, 23, 7, 5, tzinfo=UTC)


def _update(minute: int, price: str) -> TrackedEtoroMarketUpdate:
    timestamp = datetime(2026, 8, 23, 7, minute, 30, tzinfo=UTC)
    market_update = EtoroMarketUpdate(
        instrument_id=TRACKED.etoro_instrument_id,
        bid=None,
        ask=None,
        last_execution=Decimal(price),
        timestamp=timestamp,
        is_market_open=True,
        is_exchange_open=True,
        message_type="Update",
    )
    return TrackedEtoroMarketUpdate(
        tracked_instrument_id=TRACKED.tracked_instrument_id,
        instrument=TRACKED.instrument,
        market=TRACKED.market,
        etoro_instrument_id=TRACKED.etoro_instrument_id,
        update=market_update,
    )


class SyntheticEventReactionReplayTests(unittest.TestCase):
    def test_replays_drop_extension_retrace_and_reversal_through_real_pipeline(self) -> None:
        candle_pipeline = TrackedCandlePipeline()
        orchestrator = EventReactionOrchestrator()

        # 07:00-07:04 establish a flat 100 pre-event reference. After the
        # 07:05 event the synthetic market drops, extends, retraces, then
        # reverses above the reference. The 07:09 update exists only to close
        # the 07:08 candle; runtime code never flushes partial candles here.
        prices = (
            "100",
            "100",
            "100",
            "100",
            "100",
            "96",
            "94",
            "97",
            "103",
            "103",
        )

        closed: list[TrackedMarketCandle] = []
        for minute, price in enumerate(prices):
            closed.extend(candle_pipeline.add(_update(minute, price)))

        one_minute = tuple(candle for candle in closed if candle.interval_minutes == 1)
        five_minute = tuple(candle for candle in closed if candle.interval_minutes == 5)

        self.assertEqual(
            [candle.start.minute for candle in one_minute],
            list(range(0, 9)),
        )
        self.assertEqual(len(five_minute), 1)
        self.assertEqual(five_minute[0].start.minute, 0)
        self.assertEqual(five_minute[0].source_minutes, 5)
        self.assertEqual(five_minute[0].close, Decimal("100"))

        reference_candles = one_minute
        post_event = [candle for candle in one_minute if candle.start >= EVENT_AT]
        self.assertEqual([candle.start.minute for candle in post_event], [5, 6, 7, 8])

        observations = []
        for candle in post_event:
            result = orchestrator.add(
                event_id="synthetic-event-1",
                event_at=EVENT_AT,
                tracked=TRACKED,
                reference_candles=reference_candles,
                reaction_candle=candle,
            )
            self.assertIsNotNone(result)
            assert result is not None
            observations.append(result)

        self.assertTrue(
            all(
                item.reference.source_candle_start
                == datetime(2026, 8, 23, 7, 4, tzinfo=UTC)
                for item in observations
            )
        )
        self.assertTrue(
            all(item.reference.baseline.reference_price == Decimal("100") for item in observations)
        )

        self.assertEqual(
            [item.reaction.tracked_reaction.reaction.direction for item in observations],
            [
                ReactionDirection.NEGATIVE,
                ReactionDirection.NEGATIVE,
                ReactionDirection.NEGATIVE,
                ReactionDirection.POSITIVE,
            ],
        )
        self.assertEqual(
            [item.reaction.tracked_reaction.evolution.evolution for item in observations],
            [
                ReactionEvolution.INITIAL,
                ReactionEvolution.EXTENDING,
                ReactionEvolution.RETRACING,
                ReactionEvolution.REVERSAL,
            ],
        )
        self.assertEqual(
            [item.reaction.tracked_reaction.reaction.return_pct for item in observations],
            [
                Decimal("-4.00"),
                Decimal("-6.00"),
                Decimal("-3.00"),
                Decimal("3.00"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
