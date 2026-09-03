from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from trading_system.market_open_evidence import _canonical_raw_text, _pattern_from_raw_text
from trading_system.market_open_paper import _provider_symbol, detect_market_open_pattern
from trading_system.market_reaction import DEFAULT_FLAT_THRESHOLD_PCT
from trading_system.models import Direction, EventExpectation
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


OPEN_AT = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
REFERENCE = Decimal("10.00")


def market_open_event(
    *,
    previous_return: str = "-2.00",
    reference_captured_at: datetime | None = None,
    reference_kind: str = "etoro_last_execution_pre_event_snapshot",
) -> PersistentTrackedEvent:
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
        resolved_etoro_instrument_id=123,
        resolved_etoro_symbol="BHP.ASX",
        resolved_etoro_display_name="BHP Group Limited",
        resolved_etoro_market="Sydney",
        reference_price=REFERENCE,
        reference_captured_at=reference_captured_at or OPEN_AT - timedelta(minutes=1),
        reference_kind=reference_kind,
        reaction_anchor_at=OPEN_AT,
        pre_event_market_context={"close_to_close_return_pct": previous_return},
    )


def reaction(minute: int, return_pct: str) -> TrackedEventReactionRecord:
    pct = Decimal(return_pct)
    close = REFERENCE * (Decimal("1") + pct / Decimal("100"))
    if pct > DEFAULT_FLAT_THRESHOLD_PCT:
        direction = "positive"
    elif pct < -DEFAULT_FLAT_THRESHOLD_PCT:
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
            reactions=(reaction(0, "-1.00"), reaction(1, "0.40"), reaction(2, "0.70")),
        )
        self.assertIsNotNone(pattern)
        assert pattern is not None
        self.assertIs(pattern.direction, Direction.LONG)
        self.assertEqual(pattern.setup.score, 35)
        self.assertEqual(pattern.confirmation.score, 25)
        self.assertEqual(pattern.reaction_pct, Decimal("0.70"))
        self.assertEqual(pattern.execution_price, Decimal("10.0700"))

    def test_later_negative_reaction_invalidates_earlier_long(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(
                reaction(0, "-1.00"),
                reaction(1, "0.40"),
                reaction(2, "0.70"),
                reaction(3, "-0.80"),
            ),
        )
        self.assertIsNot(pattern.direction if pattern else None, Direction.LONG)

    def test_single_positive_reclaim_does_not_create_long(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(reaction(0, "-1.00"), reaction(1, "0.40"), reaction(2, "0.10")),
        )
        self.assertIsNone(pattern)

    def test_short_requires_bounce_then_renewed_negative_move(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(reaction(0, "-1.00"), reaction(1, "-0.40"), reaction(2, "-0.80")),
        )
        self.assertIsNotNone(pattern)
        assert pattern is not None
        self.assertIs(pattern.direction, Direction.SHORT)
        self.assertEqual(pattern.setup.score, 35)
        self.assertEqual(pattern.confirmation.score, 25)
        self.assertEqual(pattern.reaction_pct, Decimal("-0.80"))
        self.assertEqual(pattern.execution_price, Decimal("9.9200"))

    def test_later_positive_reaction_invalidates_earlier_short(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(
                reaction(0, "-1.00"),
                reaction(1, "-0.40"),
                reaction(2, "-0.80"),
                reaction(3, "0.30"),
            ),
        )
        self.assertIsNot(pattern.direction if pattern else None, Direction.SHORT)

    def test_frozen_evidence_roundtrips_confirming_execution_price(self) -> None:
        event = market_open_event()
        reactions = (reaction(0, "-1.00"), reaction(1, "0.40"), reaction(2, "0.70"))
        pattern = detect_market_open_pattern(event=event, reactions=reactions)
        self.assertIsNotNone(pattern)
        assert pattern is not None
        expectation = EventExpectation(
            event_id="tracked:bhp-open-1",
            instrument="BHP.ASX",
            event_name="BHP.ASX market open",
            scheduled_date=date(2026, 9, 3),
            version=1,
        )
        raw_text = _canonical_raw_text(
            event=event,
            expectation=expectation,
            pattern=pattern,
            reactions=reactions,
        )
        restored = _pattern_from_raw_text(
            raw_text,
            event=event,
            expectation=expectation,
        )
        self.assertEqual(restored.execution_price, pattern.execution_price)
        self.assertEqual(restored.reaction_pct, pattern.reaction_pct)
        self.assertIs(restored.direction, pattern.direction)

    def test_previous_session_must_be_bearish_for_this_setup(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(previous_return="0.50"),
            reactions=(reaction(0, "-1.00"), reaction(1, "0.40"), reaction(2, "0.70")),
        )
        self.assertIsNone(pattern)

    def test_open_must_start_with_real_weakness(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(reaction(0, "0.20"), reaction(1, "0.40"), reaction(2, "0.70")),
        )
        self.assertIsNone(pattern)

    def test_reactions_outside_first_thirty_minutes_are_ignored(self) -> None:
        pattern = detect_market_open_pattern(
            event=market_open_event(),
            reactions=(reaction(0, "-1.00"), reaction(1, "0.40"), reaction(31, "0.70")),
        )
        self.assertIsNone(pattern)

    def test_duplicate_one_minute_candle_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            detect_market_open_pattern(
                event=market_open_event(),
                reactions=(reaction(0, "-1.00"), reaction(1, "0.40"), reaction(1, "0.70")),
            )

    def test_reference_captured_after_open_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "captured after market open"):
            detect_market_open_pattern(
                event=market_open_event(reference_captured_at=OPEN_AT + timedelta(seconds=1)),
                reactions=(reaction(0, "-1.00"), reaction(1, "0.40"), reaction(2, "0.70")),
            )

    def test_noncanonical_reference_kind_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "not canonical"):
            detect_market_open_pattern(
                event=market_open_event(reference_kind="manual"),
                reactions=(reaction(0, "-1.00"), reaction(1, "0.40"), reaction(2, "0.70")),
            )

    def test_provider_symbol_requires_exact_canonical_broker_symbol(self) -> None:
        event = replace(market_open_event(), resolved_etoro_symbol="BHP")
        with self.assertRaisesRegex(ValueError, "differs from canonical instrument"):
            _provider_symbol(event)


if __name__ == "__main__":
    unittest.main()
