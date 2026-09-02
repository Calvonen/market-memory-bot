from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from trading_system.market_open_paper import detect_market_open_pattern
from trading_system.models import Direction
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


OPEN_AT = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
REFERENCE = Decimal("10.00")


def market_open_event(*, previous_return: str = "-2.00") -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="bhp-open-1",
        tracked_instrument_id="bhp-tracked",
        calendar_event_id=None,
        company_name="BHP Group Limited",
        instrument="BHP.ASX",
        market="AUSTRALIA",
        source="manual",
        external_key="bhp-asx-open-2026-09-03",
        kind="market_open",
        title="BHP ASX market open",
        event_at=OPEN_AT,
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.MONITORING,
        reference_price=REFERENCE,
        reaction_anchor_at=OPEN_AT,
        pre_event_market_context={"close_to_close_return_pct": previous_return},
    )


def reaction(minute: int, return_pct: str) -> TrackedEventReactionRecord:
    pct = Decimal(return_pct)
    close = REFERENCE * (Decimal("1") + pct / Decimal("100"))
    if pct > Decimal("0.15"):
        direction = "positive"
    elif pct < Decimal("-0.15"):
        direction = "negative"
    else:
        direction = "flat"
    start = OPEN_AT + timedelta(minutes=minute)
    return TrackedEventReactionRecord(
        tracked_market_event_id="bhp-open-1",
        interval_minutes=1,
        candle_start=start,
        reference_price=REFERENCE,
        close_price=close,
        return_pct=pct,
        direction=direction,
        evolution="opening",
        observed_at=start + timedelta(minutes=1),
    )


class MarketOpenPatternTests(unittest.TestCase):
    def test_long_requires_reversal_and_positive_follow_through(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(
                reaction(0, "-1.00"),
                reaction(1, "0.40"),
                reaction(2, "0.70"),
            ),
        )
        self.assertIsNotNone(pattern)
        assert pattern is not None
        self.assertIs(pattern.direction, Direction.LONG)
        self.assertEqual(pattern.setup.score, 35)
        self.assertEqual(pattern.confirmation.score, 25)
        self.assertEqual(pattern.reaction_pct, Decimal("0.70"))

    def test_single_positive_reclaim_does_not_create_long(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(
                reaction(0, "-1.00"),
                reaction(1, "0.40"),
                reaction(2, "0.10"),
            ),
        )
        self.assertIsNone(pattern)

    def test_short_requires_bounce_then_renewed_negative_move(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(
                reaction(0, "-1.00"),
                reaction(1, "-0.40"),
                reaction(2, "-0.80"),
            ),
        )
        self.assertIsNotNone(pattern)
        assert pattern is not None
        self.assertIs(pattern.direction, Direction.SHORT)
        self.assertEqual(pattern.setup.score, 35)
        self.assertEqual(pattern.confirmation.score, 25)
        self.assertEqual(pattern.reaction_pct, Decimal("-0.80"))

    def test_previous_session_must_be_bearish_for_this_setup(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(previous_return="0.50"),
            reactions=(
                reaction(0, "-1.00"),
                reaction(1, "0.40"),
                reaction(2, "0.70"),
            ),
        )
        self.assertIsNone(pattern)

    def test_open_must_start_with_real_weakness(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(
                reaction(0, "0.20"),
                reaction(1, "0.40"),
                reaction(2, "0.70"),
            ),
        )
        self.assertIsNone(pattern)

    def test_reactions_outside_first_thirty_minutes_are_ignored(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(
                reaction(0, "-1.00"),
                reaction(1, "0.40"),
                reaction(31, "0.70"),
            ),
        )
        self.assertIsNone(pattern)

    def test_duplicate_one_minute_candle_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            detect_market_open_pattern(
                event=market_open_event(),
                reactions=(
                    reaction(0, "-1.00"),
                    reaction(1, "0.40"),
                    reaction(1, "0.70"),
                ),
            )


if __name__ == "__main__":
    unittest.main()
