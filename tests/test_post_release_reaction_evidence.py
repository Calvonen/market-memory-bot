from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.post_release_reaction_evidence import (
    canonical_post_release_reaction_evidence,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


ANCHOR = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)


def _event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="event-1",
        tracked_instrument_id="instrument-1",
        calendar_event_id=None,
        company_name="Example",
        instrument="EXM",
        market="NASDAQ",
        source="manual_ir",
        external_key="fy26",
        kind="earnings",
        title="FY26 results",
        event_at=ANCHOR,
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.MONITORING,
        reference_price=Decimal("100"),
        reaction_anchor_at=ANCHOR,
    )


def _reaction(
    *,
    interval_minutes: int,
    candle_start: datetime,
    event_id: str = "event-1",
) -> TrackedEventReactionRecord:
    return TrackedEventReactionRecord(
        tracked_market_event_id=event_id,
        interval_minutes=interval_minutes,
        candle_start=candle_start,
        reference_price=Decimal("100"),
        close_price=Decimal("101"),
        return_pct=Decimal("1"),
        direction="positive",
        evolution="continuation",
        observed_at=candle_start + timedelta(minutes=interval_minutes),
    )


class PostReleaseReactionEvidenceTests(unittest.TestCase):
    def test_selects_canonical_5m_and_15m_evidence_after_initial_window(self) -> None:
        closing_1m = _reaction(
            interval_minutes=1,
            candle_start=ANCHOR + timedelta(minutes=29),
        )
        first_5m = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=30),
        )
        boundary_5m = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=145),
        )
        first_15m = _reaction(
            interval_minutes=15,
            candle_start=ANCHOR + timedelta(minutes=150),
        )

        selected = canonical_post_release_reaction_evidence(
            event=_event(),
            reactions=(first_15m, closing_1m, boundary_5m, first_5m),
        )

        self.assertEqual(selected, (first_5m, boundary_5m, first_15m))

    def test_noncanonical_interval_after_30_minutes_fails_closed(self) -> None:
        wrong_1m = _reaction(
            interval_minutes=1,
            candle_start=ANCHOR + timedelta(minutes=30),
        )

        with self.assertRaisesRegex(ValueError, "canonical monitoring profile"):
            canonical_post_release_reaction_evidence(
                event=_event(),
                reactions=(wrong_1m,),
            )

    def test_inconsistent_persisted_return_fails_closed(self) -> None:
        first_5m = replace(
            _reaction(
                interval_minutes=5,
                candle_start=ANCHOR + timedelta(minutes=30),
            ),
            return_pct=Decimal("2"),
        )

        with self.assertRaisesRegex(ValueError, "return differs from stored prices"):
            canonical_post_release_reaction_evidence(
                event=_event(),
                reactions=(first_5m,),
            )

    def test_duplicate_canonical_evidence_fails_closed(self) -> None:
        first_5m = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=30),
        )

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            canonical_post_release_reaction_evidence(
                event=_event(),
                reactions=(first_5m, first_5m),
            )

    def test_unrelated_event_rows_are_ignored(self) -> None:
        other = _reaction(
            event_id="event-2",
            interval_minutes=1,
            candle_start=ANCHOR + timedelta(minutes=30),
        )

        self.assertEqual(
            canonical_post_release_reaction_evidence(
                event=_event(),
                reactions=(other,),
            ),
            (),
        )

    def test_missing_anchor_has_no_post_release_stream(self) -> None:
        event = replace(_event(), reaction_anchor_at=None)

        self.assertEqual(
            canonical_post_release_reaction_evidence(event=event, reactions=()),
            (),
        )


if __name__ == "__main__":
    unittest.main()
