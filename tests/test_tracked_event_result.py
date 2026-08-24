from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.tracked_event_repository import TrackedEventReactionRecord
from trading_system.tracked_event_result import latest_tracked_event_reaction


def _reaction(
    *,
    candle_start: datetime,
    observed_at: datetime,
    interval_minutes: int = 1,
    reference_price: str = "100.00",
    close_price: str = "101.25",
    return_pct: str = "1.25",
    direction: str = "up",
    evolution: str = "extending",
) -> TrackedEventReactionRecord:
    return TrackedEventReactionRecord(
        tracked_market_event_id="11111111-1111-1111-1111-111111111111",
        interval_minutes=interval_minutes,
        candle_start=candle_start,
        reference_price=Decimal(reference_price),
        close_price=Decimal(close_price),
        return_pct=Decimal(return_pct),
        direction=direction,
        evolution=evolution,
        observed_at=observed_at,
    )


class TrackedEventLatestReactionTests(unittest.TestCase):
    def test_empty_reactions_have_no_latest_result(self):
        self.assertIsNone(latest_tracked_event_reaction(()))

    def test_latest_candle_start_wins_even_if_older_row_was_observed_later(self):
        base = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
        older_candle = _reaction(
            candle_start=base,
            observed_at=base + timedelta(hours=2),
            return_pct="0.5",
        )
        newer_candle = _reaction(
            candle_start=base + timedelta(minutes=1),
            observed_at=base + timedelta(minutes=2),
            return_pct="1.75",
        )

        result = latest_tracked_event_reaction((newer_candle, older_candle))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.candle_start, newer_candle.candle_start)
        self.assertEqual(result.return_pct, Decimal("1.75"))

    def test_ties_are_deterministic_by_observed_at_then_interval(self):
        base = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
        first = _reaction(
            candle_start=base,
            observed_at=base + timedelta(minutes=5),
            interval_minutes=1,
            return_pct="1.0",
        )
        later_observation = _reaction(
            candle_start=base,
            observed_at=base + timedelta(minutes=6),
            interval_minutes=1,
            return_pct="2.0",
        )
        larger_interval_same_observation = _reaction(
            candle_start=base,
            observed_at=base + timedelta(minutes=6),
            interval_minutes=5,
            return_pct="3.0",
        )

        result = latest_tracked_event_reaction(
            (larger_interval_same_observation, first, later_observation)
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.interval_minutes, 5)
        self.assertEqual(result.return_pct, Decimal("3.0"))

    def test_persisted_values_are_copied_without_rounding_or_recalculation(self):
        base = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
        row = _reaction(
            candle_start=base,
            observed_at=base + timedelta(minutes=1),
            interval_minutes=15,
            reference_price="7.123456",
            close_price="7.987654",
            return_pct="12.3456789",
            direction="up",
            evolution="extending",
        )

        result = latest_tracked_event_reaction((row,))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reference_price, Decimal("7.123456"))
        self.assertEqual(result.close_price, Decimal("7.987654"))
        self.assertEqual(result.return_pct, Decimal("12.3456789"))
        self.assertEqual(result.direction, "up")
        self.assertEqual(result.evolution, "extending")
        self.assertEqual(result.observed_at, row.observed_at)


if __name__ == "__main__":
    unittest.main()
