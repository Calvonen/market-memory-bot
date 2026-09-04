from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from trading_system.etoro_instrument_resolver import ResolvedEtoroInstrument
from trading_system.models import EventExpectation
from trading_system.tracked_event_paper_bridge import build_tracked_event_price_confirmation
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


EVENT_AT = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
ANCHOR_AT = EVENT_AT


class FakeResolver:
    def resolve(self, request):
        return ResolvedEtoroInstrument(
            instrument_id=123,
            symbol="AVGO",
            display_name="Broadcom Inc",
            market="NASDAQ",
        )


def event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="tracked-avgo",
        tracked_instrument_id="instrument-avgo",
        calendar_event_id=None,
        company_name="Broadcom Inc",
        instrument="AVGO",
        market="NASDAQ",
        source="calendar",
        external_key="avgo-fy26",
        kind="earnings",
        title="FY26 results",
        event_at=EVENT_AT,
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.MONITORING,
        resolved_etoro_instrument_id=123,
        resolved_etoro_symbol="AVGO",
        resolved_etoro_display_name="Broadcom Inc",
        resolved_etoro_market="NASDAQ",
        resolution_armed_at=EVENT_AT - timedelta(minutes=2),
        resolution_armed_by="tracked-event-preflight",
        reference_price=Decimal("10.00"),
        reference_captured_at=EVENT_AT - timedelta(minutes=1),
        reference_kind="etoro_last_execution_pre_event_snapshot",
        reaction_anchor_at=ANCHOR_AT,
    )


def expectation() -> EventExpectation:
    return EventExpectation(
        event_id="tracked:tracked-avgo",
        instrument="AVGO",
        event_name="FY26 results",
        scheduled_date=date(2026, 9, 2),
    )


def reaction(
    *,
    minute: int,
    close_price: Decimal,
    return_pct: Decimal,
    direction: str,
    observed_delay_seconds: int = 60,
) -> TrackedEventReactionRecord:
    candle_start = ANCHOR_AT + timedelta(minutes=minute)
    return TrackedEventReactionRecord(
        tracked_market_event_id="tracked-avgo",
        interval_minutes=1,
        candle_start=candle_start,
        reference_price=Decimal("10.00"),
        close_price=close_price,
        return_pct=return_pct,
        direction=direction,
        evolution="initial" if minute == 0 else "followup",
        observed_at=candle_start + timedelta(seconds=observed_delay_seconds),
    )


class EarningsLaterOneMinuteConfirmationTests(unittest.TestCase):
    def test_flat_anchor_uses_first_later_positive_one_minute_reaction(self) -> None:
        selected = build_tracked_event_price_confirmation(
            event=event(),
            expectation=expectation(),
            reactions=(
                reaction(
                    minute=0,
                    close_price=Decimal("10.01"),
                    return_pct=Decimal("0.10"),
                    direction="flat",
                ),
                reaction(
                    minute=2,
                    close_price=Decimal("10.20"),
                    return_pct=Decimal("2.00"),
                    direction="positive",
                ),
            ),
            resolver=FakeResolver(),
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.candle_start, ANCHOR_AT + timedelta(minutes=2))
        self.assertEqual(selected.direction, "positive")
        self.assertEqual(selected.return_pct, Decimal("2.00"))

    def test_flat_anchor_uses_first_later_negative_one_minute_reaction(self) -> None:
        selected = build_tracked_event_price_confirmation(
            event=event(),
            expectation=expectation(),
            reactions=(
                reaction(
                    minute=0,
                    close_price=Decimal("10.01"),
                    return_pct=Decimal("0.10"),
                    direction="flat",
                ),
                reaction(
                    minute=3,
                    close_price=Decimal("9.80"),
                    return_pct=Decimal("-2.00"),
                    direction="negative",
                ),
            ),
            resolver=FakeResolver(),
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.candle_start, ANCHOR_AT + timedelta(minutes=3))
        self.assertEqual(selected.direction, "negative")

    def test_first_later_nonflat_is_deterministic_even_if_input_is_unsorted(self) -> None:
        selected = build_tracked_event_price_confirmation(
            event=event(),
            expectation=expectation(),
            reactions=(
                reaction(
                    minute=5,
                    close_price=Decimal("9.80"),
                    return_pct=Decimal("-2.00"),
                    direction="negative",
                ),
                reaction(
                    minute=0,
                    close_price=Decimal("10.01"),
                    return_pct=Decimal("0.10"),
                    direction="flat",
                ),
                reaction(
                    minute=2,
                    close_price=Decimal("10.20"),
                    return_pct=Decimal("2.00"),
                    direction="positive",
                ),
            ),
            resolver=FakeResolver(),
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.candle_start, ANCHOR_AT + timedelta(minutes=2))
        self.assertEqual(selected.direction, "positive")

    def test_reaction_after_bounded_window_does_not_confirm(self) -> None:
        selected = build_tracked_event_price_confirmation(
            event=event(),
            expectation=expectation(),
            reactions=(
                reaction(
                    minute=0,
                    close_price=Decimal("10.01"),
                    return_pct=Decimal("0.10"),
                    direction="flat",
                ),
                reaction(
                    minute=30,
                    close_price=Decimal("10.20"),
                    return_pct=Decimal("2.00"),
                    direction="positive",
                ),
            ),
            resolver=FakeResolver(),
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.candle_start, ANCHOR_AT)
        self.assertEqual(selected.direction, "flat")

    def test_directional_anchor_does_not_depend_on_later_reaction_validity(self) -> None:
        selected = build_tracked_event_price_confirmation(
            event=event(),
            expectation=expectation(),
            reactions=(
                reaction(
                    minute=0,
                    close_price=Decimal("10.20"),
                    return_pct=Decimal("2.00"),
                    direction="positive",
                ),
                reaction(
                    minute=2,
                    close_price=Decimal("10.20"),
                    return_pct=Decimal("99.00"),
                    direction="positive",
                ),
            ),
            resolver=FakeResolver(),
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.candle_start, ANCHOR_AT)
        self.assertEqual(selected.direction, "positive")


if __name__ == "__main__":
    unittest.main()
