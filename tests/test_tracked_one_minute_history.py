from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_one_minute_history import TrackedOneMinuteHistory


def _candle(
    minute: int,
    close: str,
    *,
    interval: int = 1,
    source_minutes: int | None = None,
    tracked_id: str = "tracked-1",
    instrument: str = "ABC",
    market: str = "LSE",
    etoro_id: int = 123,
) -> TrackedMarketCandle:
    price = Decimal(close)
    return TrackedMarketCandle(
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        interval_minutes=interval,
        start=datetime(2026, 8, 23, 7, minute, tzinfo=UTC),
        open=price,
        high=price,
        low=price,
        close=price,
        source_minutes=interval if source_minutes is None else source_minutes,
    )


class TrackedOneMinuteHistoryTests(unittest.TestCase):
    def test_keeps_only_complete_one_minute_candles(self) -> None:
        history = TrackedOneMinuteHistory()
        history.add((_candle(1, "100"), _candle(0, "99", interval=5)))

        self.assertEqual(history.candles_for("tracked-1"), (_candle(1, "100"),))

    def test_history_is_bounded_per_tracked_instrument(self) -> None:
        history = TrackedOneMinuteHistory(max_candles_per_instrument=2)
        history.add((_candle(1, "100"), _candle(2, "101"), _candle(3, "102")))

        self.assertEqual(
            history.candles_for("tracked-1"),
            (_candle(2, "101"), _candle(3, "102")),
        )

    def test_histories_are_isolated_by_tracked_instrument(self) -> None:
        history = TrackedOneMinuteHistory()
        other = _candle(1, "50", tracked_id="tracked-2", instrument="XYZ", etoro_id=456)
        history.add((_candle(1, "100"), other))

        self.assertEqual(history.candles_for("tracked-1"), (_candle(1, "100"),))
        self.assertEqual(history.candles_for("tracked-2"), (other,))

    def test_identical_duplicate_is_idempotent_but_conflict_fails_closed(self) -> None:
        history = TrackedOneMinuteHistory()
        original = _candle(1, "100")
        history.add((original,))
        history.add((original,))

        self.assertEqual(history.candles_for("tracked-1"), (original,))
        with self.assertRaisesRegex(ValueError, "conflicting one-minute candle"):
            history.add((_candle(1, "101"),))

    def test_identity_change_fails_closed(self) -> None:
        history = TrackedOneMinuteHistory()
        history.add((_candle(1, "100"),))

        with self.assertRaisesRegex(ValueError, "identity changed"):
            history.add((_candle(2, "101", etoro_id=999),))

    def test_out_of_order_new_candle_fails_closed(self) -> None:
        history = TrackedOneMinuteHistory()
        history.add((_candle(2, "100"),))

        with self.assertRaisesRegex(ValueError, "strictly increasing order"):
            history.add((_candle(1, "99"),))

    def test_incomplete_one_minute_candle_fails_closed(self) -> None:
        history = TrackedOneMinuteHistory()

        with self.assertRaisesRegex(ValueError, "complete one-minute"):
            history.add((_candle(1, "100", source_minutes=0),))

    def test_invalid_capacity_and_blank_lookup_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be positive"):
            TrackedOneMinuteHistory(max_candles_per_instrument=0)

        history = TrackedOneMinuteHistory()
        with self.assertRaisesRegex(ValueError, "must not be blank"):
            history.candles_for("   ")


if __name__ == "__main__":
    unittest.main()
