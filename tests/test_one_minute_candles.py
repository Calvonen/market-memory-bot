import unittest
from datetime import datetime, timezone
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.one_minute_candles import OneMinuteCandle, OneMinuteCandleBuilder


def _update(
    timestamp: str | None,
    *,
    instrument_id: int = 2619,
    last: str | None = None,
    bid: str | None = None,
    ask: str | None = None,
) -> EtoroMarketUpdate:
    return EtoroMarketUpdate(
        instrument_id=instrument_id,
        bid=Decimal(bid) if bid is not None else None,
        ask=Decimal(ask) if ask is not None else None,
        last_execution=Decimal(last) if last is not None else None,
        timestamp=datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if timestamp else None,
        is_market_open=True,
        is_exchange_open=True,
        message_type="Update",
    )


class OneMinuteCandleBuilderTests(unittest.TestCase):
    def test_builds_ohlc_and_closes_on_next_minute(self) -> None:
        builder = OneMinuteCandleBuilder(2619)

        self.assertEqual(builder.add(_update("2026-08-24T07:00:05Z", last="71.20")), ())
        self.assertEqual(builder.add(_update("2026-08-24T07:00:20Z", last="71.60")), ())
        self.assertEqual(builder.add(_update("2026-08-24T07:00:35Z", last="71.10")), ())
        self.assertEqual(builder.add(_update("2026-08-24T07:00:55Z", last="71.40")), ())

        closed = builder.add(_update("2026-08-24T07:01:01Z", last="71.50"))

        self.assertEqual(
            closed,
            (
                OneMinuteCandle(
                    instrument_id=2619,
                    start=datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc),
                    open=Decimal("71.20"),
                    high=Decimal("71.60"),
                    low=Decimal("71.10"),
                    close=Decimal("71.40"),
                ),
            ),
        )

    def test_prefers_last_execution_then_midpoint_then_one_sided_quote(self) -> None:
        builder = OneMinuteCandleBuilder(2619)
        builder.add(_update("2026-08-24T07:00:01Z", last="71.30", bid="70", ask="80"))
        builder.add(_update("2026-08-24T07:00:10Z", bid="71.20", ask="71.40"))
        builder.add(_update("2026-08-24T07:00:20Z", bid="71.10"))

        candle = builder.add(_update("2026-08-24T07:01:00Z", ask="71.50"))[0]

        self.assertEqual(candle.open, Decimal("71.30"))
        self.assertEqual(candle.high, Decimal("71.30"))
        self.assertEqual(candle.low, Decimal("71.10"))
        self.assertEqual(candle.close, Decimal("71.10"))

    def test_ignores_wrong_instrument_missing_timestamp_and_missing_price(self) -> None:
        builder = OneMinuteCandleBuilder(2619)

        self.assertEqual(builder.add(_update("2026-08-24T07:00:00Z", instrument_id=999, last="1")), ())
        self.assertEqual(builder.add(_update(None, last="71.20")), ())
        self.assertEqual(builder.add(_update("2026-08-24T07:00:00Z")), ())
        self.assertIsNone(builder.flush())

    def test_ignores_late_updates_for_an_already_closed_minute(self) -> None:
        builder = OneMinuteCandleBuilder(2619)
        builder.add(_update("2026-08-24T07:00:05Z", last="71.20"))
        first = builder.add(_update("2026-08-24T07:01:05Z", last="71.50"))

        self.assertEqual(len(first), 1)
        self.assertEqual(builder.add(_update("2026-08-24T07:00:59Z", last="99.00")), ())

        current = builder.flush()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.start, datetime(2026, 8, 24, 7, 1, tzinfo=timezone.utc))
        self.assertEqual(current.open, Decimal("71.50"))
        self.assertEqual(current.high, Decimal("71.50"))
        self.assertEqual(current.low, Decimal("71.50"))
        self.assertEqual(current.close, Decimal("71.50"))

    def test_gap_closes_prior_candle_without_creating_synthetic_empty_minutes(self) -> None:
        builder = OneMinuteCandleBuilder(2619)
        builder.add(_update("2026-08-24T07:00:05Z", last="71.20"))

        closed = builder.add(_update("2026-08-24T07:04:05Z", last="72.00"))

        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].start, datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc))
        current = builder.flush()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.start, datetime(2026, 8, 24, 7, 4, tzinfo=timezone.utc))

    def test_naive_timestamp_is_treated_as_utc(self) -> None:
        builder = OneMinuteCandleBuilder(2619)
        update = _update("2026-08-24T07:00:05", last="71.20")

        builder.add(update)
        candle = builder.flush()

        self.assertIsNotNone(candle)
        assert candle is not None
        self.assertEqual(candle.start, datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
