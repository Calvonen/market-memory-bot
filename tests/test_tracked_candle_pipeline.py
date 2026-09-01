import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.tracked_candle_pipeline import TrackedCandlePipeline
from trading_system.tracked_etoro_live import TrackedEtoroMarketUpdate


def _tracked_update(
    *,
    tracked_id: str = "tracked-1",
    instrument: str = "NOKIA.HE",
    market: str = "Helsinki",
    etoro_id: int = 4242,
    update_instrument_id: int | None = None,
    minute: int = 0,
    price: str = "10.00",
) -> TrackedEtoroMarketUpdate:
    value = Decimal(price)
    return TrackedEtoroMarketUpdate(
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        update=EtoroMarketUpdate(
            instrument_id=etoro_id if update_instrument_id is None else update_instrument_id,
            bid=value - Decimal("0.01"),
            ask=value + Decimal("0.01"),
            last_execution=value,
            timestamp=datetime(2026, 8, 24, 7, 0, tzinfo=UTC) + timedelta(minutes=minute),
            is_market_open=True,
            is_exchange_open=True,
            message_type="Update",
        ),
    )


class TrackedCandlePipelineTests(unittest.TestCase):
    def test_emits_closed_one_minute_candle_with_tracked_identity(self) -> None:
        pipeline = TrackedCandlePipeline()

        self.assertEqual(pipeline.add(_tracked_update(minute=0, price="10.00")), ())
        candles = pipeline.add(_tracked_update(minute=1, price="10.50"))

        self.assertEqual(len(candles), 1)
        candle = candles[0]
        self.assertEqual(candle.tracked_instrument_id, "tracked-1")
        self.assertEqual(candle.instrument, "NOKIA.HE")
        self.assertEqual(candle.market, "Helsinki")
        self.assertEqual(candle.etoro_instrument_id, 4242)
        self.assertEqual(candle.interval_minutes, 1)
        self.assertEqual(candle.start, datetime(2026, 8, 24, 7, 0, tzinfo=UTC))
        self.assertEqual(candle.open, Decimal("10.00"))
        self.assertEqual(candle.close, Decimal("10.00"))
        self.assertEqual(candle.source_minutes, 1)

    def test_builds_complete_five_and_fifteen_minute_candles(self) -> None:
        pipeline = TrackedCandlePipeline()
        emitted = []

        for minute in range(17):
            emitted.extend(pipeline.add(_tracked_update(minute=minute, price=str(10 + minute))))

        five = [c for c in emitted if c.interval_minutes == 5]
        fifteen = [c for c in emitted if c.interval_minutes == 15]

        self.assertGreaterEqual(len(five), 1)
        self.assertEqual(five[0].start, datetime(2026, 8, 24, 7, 0, tzinfo=UTC))
        self.assertEqual(five[0].open, Decimal("10"))
        self.assertEqual(five[0].high, Decimal("14"))
        self.assertEqual(five[0].low, Decimal("10"))
        self.assertEqual(five[0].close, Decimal("14"))
        self.assertEqual(five[0].source_minutes, 5)

        self.assertEqual(len(fifteen), 1)
        self.assertEqual(fifteen[0].start, datetime(2026, 8, 24, 7, 0, tzinfo=UTC))
        self.assertEqual(fifteen[0].open, Decimal("10"))
        self.assertEqual(fifteen[0].high, Decimal("24"))
        self.assertEqual(fifteen[0].low, Decimal("10"))
        self.assertEqual(fifteen[0].close, Decimal("24"))
        self.assertEqual(fifteen[0].source_minutes, 15)

    def test_sparse_updates_preserve_source_minutes(self) -> None:
        pipeline = TrackedCandlePipeline()
        emitted = []

        emitted.extend(pipeline.add(_tracked_update(minute=0, price="10")))
        emitted.extend(pipeline.add(_tracked_update(minute=5, price="15")))
        emitted.extend(pipeline.add(_tracked_update(minute=6, price="16")))

        five = [c for c in emitted if c.interval_minutes == 5]
        self.assertEqual(len(five), 1)
        self.assertEqual(five[0].start, datetime(2026, 8, 24, 7, 0, tzinfo=UTC))
        self.assertEqual(five[0].source_minutes, 1)

    def test_interleaved_tracked_instruments_keep_independent_state(self) -> None:
        pipeline = TrackedCandlePipeline()

        pipeline.add(_tracked_update(tracked_id="a", instrument="AAA", etoro_id=1, minute=0, price="10"))
        pipeline.add(_tracked_update(tracked_id="b", instrument="BBB", etoro_id=2, minute=0, price="20"))
        a = pipeline.add(_tracked_update(tracked_id="a", instrument="AAA", etoro_id=1, minute=1, price="11"))
        b = pipeline.add(_tracked_update(tracked_id="b", instrument="BBB", etoro_id=2, minute=1, price="21"))

        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertEqual(a[0].tracked_instrument_id, "a")
        self.assertEqual(a[0].etoro_instrument_id, 1)
        self.assertEqual(a[0].open, Decimal("10"))
        self.assertEqual(b[0].tracked_instrument_id, "b")
        self.assertEqual(b[0].etoro_instrument_id, 2)
        self.assertEqual(b[0].open, Decimal("20"))

    def test_wrong_inner_instrument_id_is_ignored_without_creating_state(self) -> None:
        pipeline = TrackedCandlePipeline()

        self.assertEqual(
            pipeline.add(_tracked_update(update_instrument_id=9999, minute=0, price="99")),
            (),
        )
        self.assertEqual(pipeline.add(_tracked_update(minute=0, price="10")), ())
        candles = pipeline.add(_tracked_update(minute=1, price="11"))

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].open, Decimal("10"))

    def test_reusing_tracked_id_with_different_identity_fails_closed(self) -> None:
        pipeline = TrackedCandlePipeline()
        pipeline.add(_tracked_update(tracked_id="same", instrument="AAA", etoro_id=1, minute=0))

        with self.assertRaisesRegex(ValueError, "tracked instrument identity changed"):
            pipeline.add(_tracked_update(tracked_id="same", instrument="BBB", etoro_id=2, minute=1))

    def test_discarded_target_restarts_without_partial_candle_state(self) -> None:
        pipeline = TrackedCandlePipeline()
        pipeline.add(_tracked_update(tracked_id="a", instrument="AAA", etoro_id=1, minute=0, price="10"))

        self.assertTrue(pipeline.discard_tracked_instrument(" a "))
        self.assertIsNone(pipeline.tracked_identity("a"))
        self.assertFalse(pipeline.discard_tracked_instrument("a"))

        # Re-enabled monitoring starts fresh: minute 1 becomes the new open rather
        # than closing the partial minute-0 builder that existed before the gap.
        self.assertEqual(
            pipeline.add(_tracked_update(tracked_id="a", instrument="AAA", etoro_id=1, minute=1, price="11")),
            (),
        )
        candles = pipeline.add(
            _tracked_update(tracked_id="a", instrument="AAA", etoro_id=1, minute=2, price="12")
        )
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].open, Decimal("11"))

    def test_retain_keeps_current_targets_and_drops_removed_state(self) -> None:
        pipeline = TrackedCandlePipeline()
        pipeline.add(_tracked_update(tracked_id="a", instrument="AAA", etoro_id=1, minute=0))
        pipeline.add(_tracked_update(tracked_id="b", instrument="BBB", etoro_id=2, minute=0))

        discarded = pipeline.retain_tracked_instruments({" a "})

        self.assertEqual(discarded, ("b",))
        self.assertEqual(pipeline.tracked_identity("a"), ("AAA", "Helsinki", 1))
        self.assertIsNone(pipeline.tracked_identity("b"))

    def test_lifecycle_identity_operations_reject_blank_ids(self) -> None:
        pipeline = TrackedCandlePipeline()
        with self.assertRaisesRegex(ValueError, "tracked_instrument_id is required"):
            pipeline.discard_tracked_instrument("   ")
        with self.assertRaisesRegex(ValueError, "tracked_instrument_id is required"):
            pipeline.retain_tracked_instruments({""})
        with self.assertRaisesRegex(ValueError, "tracked_instrument_id is required"):
            pipeline.tracked_identity(" ")


if __name__ == "__main__":
    unittest.main()
