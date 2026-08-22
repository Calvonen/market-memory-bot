import unittest
from datetime import datetime, timezone
from decimal import Decimal

from trading_system.multi_minute_candles import MultiMinuteCandle, MultiMinuteCandleBuilder
from trading_system.one_minute_candles import OneMinuteCandle


def _candle(
    minute: int,
    *,
    instrument_id: int = 2619,
    open_: str = "10.0",
    high: str = "10.5",
    low: str = "9.5",
    close: str = "10.2",
) -> OneMinuteCandle:
    return OneMinuteCandle(
        instrument_id=instrument_id,
        start=datetime(2026, 8, 24, 7, minute, tzinfo=timezone.utc),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


class MultiMinuteCandleBuilderTests(unittest.TestCase):
    def test_builds_aligned_five_minute_ohlc(self) -> None:
        builder = MultiMinuteCandleBuilder(2619, 5)
        builder.add(_candle(0, open_="10.0", high="10.2", low="9.9", close="10.1"))
        builder.add(_candle(1, open_="10.1", high="10.6", low="10.0", close="10.5"))
        builder.add(_candle(2, open_="10.5", high="10.7", low="10.3", close="10.4"))
        builder.add(_candle(3, open_="10.4", high="10.5", low="9.8", close="10.0"))
        builder.add(_candle(4, open_="10.0", high="10.4", low="9.7", close="10.3"))

        closed = builder.add(_candle(5, open_="10.3", high="10.8", low="10.2", close="10.7"))

        self.assertEqual(
            closed,
            (
                MultiMinuteCandle(
                    instrument_id=2619,
                    interval_minutes=5,
                    start=datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc),
                    open=Decimal("10.0"),
                    high=Decimal("10.7"),
                    low=Decimal("9.7"),
                    close=Decimal("10.3"),
                    source_minutes=5,
                ),
            ),
        )

    def test_builds_aligned_fifteen_minute_bucket(self) -> None:
        builder = MultiMinuteCandleBuilder(2619, 15)
        builder.add(_candle(14, open_="11", high="12", low="10", close="11.5"))

        closed = builder.add(_candle(15, open_="11.5", high="12.5", low="11", close="12"))

        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].start, datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc))
        current = builder.flush()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.start, datetime(2026, 8, 24, 7, 15, tzinfo=timezone.utc))

    def test_sparse_bucket_does_not_synthesize_missing_minutes(self) -> None:
        builder = MultiMinuteCandleBuilder(2619, 5)
        builder.add(_candle(0, open_="10", high="10.2", low="9.9", close="10.1"))
        builder.add(_candle(4, open_="10.1", high="10.4", low="10.0", close="10.3"))

        candle = builder.add(_candle(5))[0]

        self.assertEqual(candle.source_minutes, 2)
        self.assertEqual(candle.open, Decimal("10"))
        self.assertEqual(candle.close, Decimal("10.3"))

    def test_same_bucket_out_of_order_minutes_set_open_and_close_by_time(self) -> None:
        builder = MultiMinuteCandleBuilder(2619, 5)
        builder.add(_candle(2, open_="10.2", high="10.4", low="10.1", close="10.3"))
        builder.add(_candle(4, open_="10.4", high="10.7", low="10.3", close="10.6"))
        builder.add(_candle(1, open_="9.8", high="10.1", low="9.7", close="10.0"))

        candle = builder.add(_candle(5))[0]

        self.assertEqual(candle.open, Decimal("9.8"))
        self.assertEqual(candle.close, Decimal("10.6"))
        self.assertEqual(candle.high, Decimal("10.7"))
        self.assertEqual(candle.low, Decimal("9.7"))
        self.assertEqual(candle.source_minutes, 3)

    def test_late_prior_bucket_and_wrong_instrument_are_ignored(self) -> None:
        builder = MultiMinuteCandleBuilder(2619, 5)
        builder.add(_candle(5))

        self.assertEqual(builder.add(_candle(4)), ())
        self.assertEqual(builder.add(_candle(6, instrument_id=9999)), ())

        current = builder.flush()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.source_minutes, 1)

    def test_naive_start_is_treated_as_utc(self) -> None:
        builder = MultiMinuteCandleBuilder(2619, 5)
        candle = OneMinuteCandle(
            instrument_id=2619,
            start=datetime(2026, 8, 24, 7, 3),
            open=Decimal("10"),
            high=Decimal("10"),
            low=Decimal("10"),
            close=Decimal("10"),
        )
        builder.add(candle)

        current = builder.flush()

        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.start, datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc))

    def test_only_five_and_fifteen_minute_intervals_are_supported(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported interval_minutes"):
            MultiMinuteCandleBuilder(2619, 10)


if __name__ == "__main__":
    unittest.main()
