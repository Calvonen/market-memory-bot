from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_etoro_live import TrackedEtoroMarketUpdate
from trading_system.tracked_event_reaction_runtime import TrackedEventReactionRuntime
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.tracked_one_minute_history import TrackedOneMinuteHistory


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    etoro_instrument_id=123,
    etoro_symbol="ABC.L",
    etoro_display_name="ABC plc",
)
EVENT = MarketEvent(
    event_id="news-1",
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    event_at=datetime(2026, 8, 23, 7, 5, 30, tzinfo=UTC),
    source=MarketEventSource.NEWS,
    kind=MarketEventKind.NEWS,
    title="Unexpected intraday news",
)


def _candle(minute: int, close: str, *, interval: int = 1) -> TrackedMarketCandle:
    price = Decimal(close)
    return TrackedMarketCandle(
        tracked_instrument_id="tracked-1",
        instrument="ABC",
        market="LSE",
        etoro_instrument_id=123,
        interval_minutes=interval,
        start=datetime(2026, 8, 23, 7, minute, tzinfo=UTC),
        open=price,
        high=price,
        low=price,
        close=price,
        source_minutes=interval,
    )


def _update(minute: int) -> TrackedEtoroMarketUpdate:
    return TrackedEtoroMarketUpdate(
        tracked_instrument_id="tracked-1",
        instrument="ABC",
        market="LSE",
        etoro_instrument_id=123,
        update=EtoroMarketUpdate(
            instrument_id=123,
            bid=Decimal("100"),
            ask=Decimal("100.1"),
            last_execution=Decimal("100.05"),
            timestamp=datetime(2026, 8, 23, 7, minute, tzinfo=UTC),
            is_market_open=True,
            is_exchange_open=True,
            message_type="rate",
        ),
    )


class _FakePipeline:
    def __init__(self, batches: tuple[tuple[TrackedMarketCandle, ...], ...]) -> None:
        self._batches = list(batches)

    def add(self, item: TrackedEtoroMarketUpdate) -> tuple[TrackedMarketCandle, ...]:
        if not self._batches:
            return ()
        return self._batches.pop(0)


class TrackedEventReactionRuntimeTests(unittest.TestCase):
    def test_continuous_history_supports_event_discovered_mid_session(self) -> None:
        pre_event = (_candle(4, "100"),)
        overlapping = (_candle(5, "99"),)
        clean = (_candle(6, "97"),)
        runtime = TrackedEventReactionRuntime(
            candle_pipeline=_FakePipeline((pre_event, overlapping, clean))
        )

        runtime.add_market_update(_update(5))
        overlap_batch = runtime.add_market_update(_update(6))

        overlap_result = runtime.observe_event(
            event=EVENT,
            tracked=TRACKED,
            reaction_candles=overlap_batch,
            observed_at=EVENT.event_at + timedelta(seconds=30),
        )
        self.assertIsNone(overlap_result)

        clean_batch = runtime.add_market_update(_update(7))
        result = runtime.observe_event(
            event=EVENT,
            tracked=TRACKED,
            reaction_candles=clean_batch,
            observed_at=EVENT.event_at + timedelta(minutes=1, seconds=30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(result.reference.source_candle_start, pre_event[0].start)
        self.assertEqual(result.reaction.tracked_reaction.reaction.candle_start, clean[0].start)
        self.assertEqual(result.reaction.tracked_reaction.reaction.close_price, Decimal("97"))

    def test_pipeline_output_is_returned_while_history_keeps_only_one_minute_candles(self) -> None:
        one = _candle(4, "100")
        five = _candle(0, "99", interval=5)
        fifteen = _candle(0, "98", interval=15)
        batch = (one, five, fifteen)
        runtime = TrackedEventReactionRuntime(candle_pipeline=_FakePipeline((batch,)))

        returned = runtime.add_market_update(_update(5))

        self.assertEqual(returned, batch)
        self.assertEqual(runtime.history.candles_for("tracked-1"), (one,))

    def test_five_minute_reaction_uses_retained_one_minute_reference(self) -> None:
        pre_event = (_candle(4, "100"),)
        five_minute = (
            TrackedMarketCandle(
                tracked_instrument_id="tracked-1",
                instrument="ABC",
                market="LSE",
                etoro_instrument_id=123,
                interval_minutes=5,
                start=datetime(2026, 8, 23, 7, 35, tzinfo=UTC),
                open=Decimal("96"),
                high=Decimal("96"),
                low=Decimal("96"),
                close=Decimal("96"),
                source_minutes=5,
            ),
        )
        runtime = TrackedEventReactionRuntime(candle_pipeline=_FakePipeline((pre_event,)))
        runtime.add_market_update(_update(5))

        result = runtime.observe_event(
            event=EVENT,
            tracked=TRACKED,
            reaction_candles=five_minute,
            observed_at=EVENT.event_at + timedelta(minutes=35),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reference.interval_minutes, 1)
        self.assertEqual(result.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(result.reaction.tracked_reaction.reaction.interval_minutes, 5)

    def test_event_without_retained_pre_event_reference_returns_none(self) -> None:
        runtime = TrackedEventReactionRuntime(candle_pipeline=_FakePipeline(()))

        result = runtime.observe_event(
            event=EVENT,
            tracked=TRACKED,
            reaction_candles=(_candle(6, "97"),),
            observed_at=EVENT.event_at + timedelta(minutes=1, seconds=30),
        )

        self.assertIsNone(result)

    def test_reaction_survives_pre_event_reference_eviction_from_bounded_history(self) -> None:
        pre_event = (_candle(4, "100"),)
        first_reaction = (_candle(6, "98"),)
        filler = (_candle(7, "95"),)
        fifteen_minute = (
            TrackedMarketCandle(
                tracked_instrument_id="tracked-1",
                instrument="ABC",
                market="LSE",
                etoro_instrument_id=123,
                interval_minutes=15,
                start=datetime(2026, 8, 23, 9, 45, tzinfo=UTC),
                open=Decimal("80"),
                high=Decimal("80"),
                low=Decimal("80"),
                close=Decimal("80"),
                source_minutes=15,
            ),
        )
        runtime = TrackedEventReactionRuntime(
            candle_pipeline=_FakePipeline((pre_event, first_reaction, filler, fifteen_minute)),
            history=TrackedOneMinuteHistory(max_candles_per_instrument=2),
        )

        runtime.add_market_update(_update(5))
        first_batch = runtime.add_market_update(_update(7))
        first_result = runtime.observe_event(
            event=EVENT,
            tracked=TRACKED,
            reaction_candles=first_batch,
            observed_at=EVENT.event_at + timedelta(seconds=90),
        )
        self.assertIsNotNone(first_result)
        assert first_result is not None
        self.assertEqual(first_result.reference.baseline.reference_price, Decimal("100"))

        # A third 1m candle pushes the bounded history past its cap of 2 and
        # evicts the original pre-event reference candle.
        runtime.add_market_update(_update(8))
        self.assertEqual(
            runtime.history.candles_for("tracked-1"),
            (first_reaction[0], filler[0]),
        )

        later_result = runtime.observe_event(
            event=EVENT,
            tracked=TRACKED,
            reaction_candles=fifteen_minute,
            observed_at=EVENT.event_at + timedelta(minutes=160),
        )

        self.assertIsNotNone(later_result)
        assert later_result is not None
        self.assertEqual(later_result.reference, first_result.reference)
        self.assertEqual(later_result.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(later_result.reaction.tracked_reaction.reaction.interval_minutes, 15)


if __name__ == "__main__":
    unittest.main()
