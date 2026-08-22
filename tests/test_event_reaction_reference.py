import unittest
from datetime import datetime, timezone
from decimal import Decimal

from trading_system.event_reaction_reference import select_event_reaction_reference
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


def _tracked() -> TrackedEtoroInstrument:
    return TrackedEtoroInstrument(
        tracked_instrument_id="tracked-1",
        instrument="TEST",
        market="LSE",
        etoro_instrument_id=123,
        etoro_symbol="TEST.L",
        etoro_display_name="Test PLC",
    )


def _candle(
    minute: int,
    close: str,
    *,
    interval: int = 1,
    source_minutes: int | None = None,
    tracked_id: str = "tracked-1",
    instrument: str = "TEST",
    market: str = "LSE",
    etoro_id: int = 123,
    tzinfo: timezone | None = timezone.utc,
) -> TrackedMarketCandle:
    price = Decimal(close)
    return TrackedMarketCandle(
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        interval_minutes=interval,
        start=datetime(2026, 8, 24, 7, minute, tzinfo=tzinfo),
        open=price,
        high=price,
        low=price,
        close=price,
        source_minutes=interval if source_minutes is None else source_minutes,
    )


class EventReactionReferenceSelectionTests(unittest.TestCase):
    def test_selects_latest_complete_candle_ending_before_event(self) -> None:
        event_at = datetime(2026, 8, 24, 7, 5, tzinfo=timezone.utc)

        result = select_event_reaction_reference(
            event_id="event-1",
            event_at=event_at,
            tracked=_tracked(),
            candles=(
                _candle(2, "98"),
                _candle(4, "99"),
                _candle(5, "101"),
            ),
        )

        assert result is not None
        self.assertEqual(result.source_candle_start.minute, 4)
        self.assertEqual(result.baseline.reference_price, Decimal("99"))
        self.assertEqual(result.baseline.event_id, "event-1")

    def test_candle_that_starts_before_but_ends_after_event_is_excluded(self) -> None:
        event_at = datetime(2026, 8, 24, 7, 4, 30, tzinfo=timezone.utc)

        result = select_event_reaction_reference(
            event_id="event-1",
            event_at=event_at,
            tracked=_tracked(),
            candles=(
                _candle(3, "98"),
                _candle(4, "99"),
            ),
        )

        assert result is not None
        self.assertEqual(result.source_candle_start.minute, 3)
        self.assertEqual(result.baseline.reference_price, Decimal("98"))

    def test_incomplete_sparse_window_is_not_eligible(self) -> None:
        event_at = datetime(2026, 8, 24, 7, 20, tzinfo=timezone.utc)

        result = select_event_reaction_reference(
            event_id="event-1",
            event_at=event_at,
            tracked=_tracked(),
            interval_minutes=5,
            candles=(
                _candle(5, "98", interval=5, source_minutes=3),
                _candle(10, "99", interval=5, source_minutes=5),
            ),
        )

        assert result is not None
        self.assertEqual(result.source_candle_start.minute, 10)
        self.assertEqual(result.baseline.reference_price, Decimal("99"))

    def test_other_identity_and_interval_are_ignored(self) -> None:
        event_at = datetime(2026, 8, 24, 7, 10, tzinfo=timezone.utc)

        result = select_event_reaction_reference(
            event_id="event-1",
            event_at=event_at,
            tracked=_tracked(),
            candles=(
                _candle(8, "200", tracked_id="other"),
                _candle(8, "300", interval=5, source_minutes=5),
                _candle(7, "99"),
            ),
        )

        assert result is not None
        self.assertEqual(result.source_candle_start.minute, 7)
        self.assertEqual(result.baseline.reference_price, Decimal("99"))

    def test_returns_none_when_no_eligible_pre_event_candle_exists(self) -> None:
        event_at = datetime(2026, 8, 24, 7, 0, 30, tzinfo=timezone.utc)

        result = select_event_reaction_reference(
            event_id="event-1",
            event_at=event_at,
            tracked=_tracked(),
            candles=(_candle(0, "99"),),
        )

        self.assertIsNone(result)

    def test_duplicate_latest_reference_candle_fails_closed(self) -> None:
        event_at = datetime(2026, 8, 24, 7, 5, tzinfo=timezone.utc)

        with self.assertRaises(ValueError):
            select_event_reaction_reference(
                event_id="event-1",
                event_at=event_at,
                tracked=_tracked(),
                candles=(
                    _candle(4, "99"),
                    _candle(4, "100"),
                ),
            )

    def test_unrelated_naive_timestamp_candle_does_not_block_valid_baseline(self) -> None:
        event_at = datetime(2026, 8, 24, 7, 5, tzinfo=timezone.utc)

        result = select_event_reaction_reference(
            event_id="event-1",
            event_at=event_at,
            tracked=_tracked(),
            candles=(
                _candle(4, "99"),
                _candle(1, "50", tracked_id="other", instrument="OTHER", tzinfo=None),
            ),
        )

        assert result is not None
        self.assertEqual(result.source_candle_start.minute, 4)
        self.assertEqual(result.baseline.reference_price, Decimal("99"))

    def test_matching_post_event_candle_with_invalid_close_does_not_block_valid_baseline(
        self,
    ) -> None:
        event_at = datetime(2026, 8, 24, 7, 5, tzinfo=timezone.utc)

        result = select_event_reaction_reference(
            event_id="event-1",
            event_at=event_at,
            tracked=_tracked(),
            candles=(
                _candle(4, "99"),
                _candle(10, "0"),
            ),
        )

        assert result is not None
        self.assertEqual(result.source_candle_start.minute, 4)
        self.assertEqual(result.baseline.reference_price, Decimal("99"))

    def test_invalid_metadata_and_eligible_price_fail_closed(self) -> None:
        aware_event = datetime(2026, 8, 24, 7, 5, tzinfo=timezone.utc)

        with self.assertRaises(ValueError):
            select_event_reaction_reference(
                event_id=" ",
                event_at=aware_event,
                tracked=_tracked(),
                candles=(),
            )
        with self.assertRaises(ValueError):
            select_event_reaction_reference(
                event_id="event-1",
                event_at=datetime(2026, 8, 24, 7, 5),
                tracked=_tracked(),
                candles=(),
            )
        with self.assertRaises(ValueError):
            select_event_reaction_reference(
                event_id="event-1",
                event_at=aware_event,
                tracked=_tracked(),
                interval_minutes=0,
                candles=(),
            )
        with self.assertRaises(ValueError):
            select_event_reaction_reference(
                event_id="event-1",
                event_at=aware_event,
                tracked=_tracked(),
                candles=(_candle(3, "0"),),
            )


if __name__ == "__main__":
    unittest.main()
