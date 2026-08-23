from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.market_event_reaction import MarketEventReactionBridge
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    etoro_instrument_id=123,
    etoro_symbol="ABC.L",
    etoro_display_name="ABC plc",
)
EVENT = MarketEvent(
    event_id="event-overlap",
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    event_at=datetime(2026, 8, 23, 7, 5, 30, tzinfo=UTC),
    source=MarketEventSource.NEWS,
    kind=MarketEventKind.NEWS,
    title="Intraday news",
)


def _candle(minute: int, close: str) -> TrackedMarketCandle:
    price = Decimal(close)
    return TrackedMarketCandle(
        tracked_instrument_id="tracked-1",
        instrument="ABC",
        market="LSE",
        etoro_instrument_id=123,
        interval_minutes=1,
        start=datetime(2026, 8, 23, 7, minute, tzinfo=UTC),
        open=price,
        high=price,
        low=price,
        close=price,
        source_minutes=1,
    )


class MarketEventReactionOverlapTests(unittest.TestCase):
    def test_overlapping_first_candle_is_ignored_until_clean_post_event_candle_closes(self) -> None:
        bridge = MarketEventReactionBridge()
        references = (_candle(4, "100"),)

        overlapping = bridge.add_for_observation(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=references,
            reaction_candles=(_candle(5, "99"),),
            observed_at=EVENT.event_at + timedelta(seconds=30),
        )
        clean = bridge.add_for_observation(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=references,
            reaction_candles=(_candle(6, "97"),),
            observed_at=EVENT.event_at + timedelta(minutes=1, seconds=30),
        )

        self.assertIsNone(overlapping)
        self.assertIsNotNone(clean)
        assert clean is not None
        self.assertEqual(clean.reference.source_candle_start, references[0].start)
        self.assertEqual(clean.reaction.tracked_reaction.reaction.interval_minutes, 1)

    def test_overlapping_and_clean_candle_in_same_batch_uses_only_the_clean_candle(self) -> None:
        bridge = MarketEventReactionBridge()
        references = (_candle(4, "100"),)
        clean_candle = _candle(6, "97")

        result = bridge.add_for_observation(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=references,
            reaction_candles=(_candle(5, "99"), clean_candle),
            observed_at=EVENT.event_at + timedelta(minutes=1, seconds=30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        snapshot = result.reaction.tracked_reaction.reaction
        self.assertEqual(snapshot.candle_start, clean_candle.start)
        self.assertEqual(snapshot.close_price, clean_candle.close)
        self.assertEqual(snapshot.interval_minutes, 1)

    def test_candle_starting_exactly_at_event_is_still_eligible(self) -> None:
        event = MarketEvent(
            event_id="event-boundary",
            tracked_instrument_id="tracked-1",
            instrument="ABC",
            market="LSE",
            event_at=datetime(2026, 8, 23, 7, 5, tzinfo=UTC),
            source=MarketEventSource.NEWS,
            kind=MarketEventKind.NEWS,
        )
        bridge = MarketEventReactionBridge()

        result = bridge.add_for_observation(
            event=event,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candles=(_candle(5, "98"),),
            observed_at=event.event_at + timedelta(minutes=1),
        )

        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
