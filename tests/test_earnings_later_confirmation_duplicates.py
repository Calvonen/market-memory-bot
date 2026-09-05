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


EVENT_AT = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
ANCHOR_AT = EVENT_AT + timedelta(hours=1)


class FakeResolver:
    def resolve(self, request):
        return ResolvedEtoroInstrument(
            instrument_id=123,
            symbol="EXM.ASX",
            display_name="Example Ltd",
            market="ASX",
        )


def _event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="tracked-event-1",
        tracked_instrument_id="instrument-1",
        calendar_event_id=None,
        company_name="Example Ltd",
        instrument="EXM.ASX",
        market="ASX",
        source="manual_ir",
        external_key="example-fy26",
        kind="earnings",
        title="FY26 results",
        event_at=EVENT_AT,
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.MONITORING,
        resolved_etoro_instrument_id=123,
        resolved_etoro_symbol="EXM.ASX",
        resolved_etoro_display_name="Example Ltd",
        resolved_etoro_market="ASX",
        resolution_armed_at=EVENT_AT - timedelta(minutes=2),
        resolution_armed_by="tracked-event-preflight",
        reference_price=Decimal("10.00"),
        reference_captured_at=EVENT_AT - timedelta(minutes=1),
        reference_kind="etoro_last_execution_pre_event_snapshot",
        reaction_anchor_at=ANCHOR_AT,
    )


def _expectation() -> EventExpectation:
    return EventExpectation(
        event_id="tracked:tracked-event-1",
        instrument="EXM.ASX",
        event_name="FY26 results",
        scheduled_date=date(2026, 8, 31),
    )


def _reaction(*, candle_start: datetime, close: str, return_pct: str, direction: str):
    return TrackedEventReactionRecord(
        tracked_market_event_id="tracked-event-1",
        interval_minutes=1,
        candle_start=candle_start,
        reference_price=Decimal("10.00"),
        close_price=Decimal(close),
        return_pct=Decimal(return_pct),
        direction=direction,
        evolution="initial",
        observed_at=candle_start + timedelta(minutes=1),
    )


class EarningsLaterConfirmationDuplicateTests(unittest.TestCase):
    def test_duplicate_earliest_later_candle_fails_closed_before_selection(self) -> None:
        duplicate_start = ANCHOR_AT + timedelta(minutes=2)
        reactions = (
            _reaction(
                candle_start=ANCHOR_AT,
                close="10.01",
                return_pct="0.10",
                direction="flat",
            ),
            _reaction(
                candle_start=duplicate_start,
                close="10.20",
                return_pct="2.00",
                direction="positive",
            ),
            _reaction(
                candle_start=duplicate_start,
                close="9.80",
                return_pct="-2.00",
                direction="negative",
            ),
        )

        with self.assertRaisesRegex(ValueError, "ambiguous confirmation-window"):
            build_tracked_event_price_confirmation(
                event=_event(),
                expectation=_expectation(),
                reactions=reactions,
                resolver=FakeResolver(),
            )


if __name__ == "__main__":
    unittest.main()
