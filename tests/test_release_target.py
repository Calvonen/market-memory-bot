from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from trading_system.market_event import MarketEventKind, MarketEventSource
from trading_system.release_target import ReleaseTarget


class ReleaseTargetTests(unittest.TestCase):
    def test_preserves_producer_metadata_without_calendar_identity(self) -> None:
        target = ReleaseTarget(
            tracked_event_id="tracked-event-1",
            tracked_instrument_id="tracked-instrument-1",
            instrument="hvn.asx",
            market="sydney",
            event_at=datetime(2026, 8, 27, 23, 15, tzinfo=UTC),
            event_date=date(2026, 8, 28),
            source=MarketEventSource.MANUAL,
            kind=MarketEventKind.EARNINGS,
            title=" Harvey Norman FY26 results ",
        )

        self.assertEqual(target.tracked_event_id, "tracked-event-1")
        self.assertEqual(target.instrument, "HVN.ASX")
        self.assertEqual(target.market, "SYDNEY")
        self.assertEqual(target.event_date, date(2026, 8, 28))
        self.assertEqual(target.source, MarketEventSource.MANUAL)
        self.assertEqual(target.kind, MarketEventKind.EARNINGS)
        self.assertEqual(target.title, "Harvey Norman FY26 results")

    def test_event_date_is_explicit_and_not_derived_from_utc_timestamp(self) -> None:
        target = ReleaseTarget(
            tracked_event_id="tracked-event-1",
            tracked_instrument_id="tracked-instrument-1",
            instrument="HVN.ASX",
            market="Sydney",
            event_at=datetime(2026, 8, 27, 23, 15, tzinfo=UTC),
            event_date=date(2026, 8, 28),
            source=MarketEventSource.MANUAL,
            kind=MarketEventKind.EARNINGS,
        )

        self.assertNotEqual(target.event_at.date(), target.event_date)

    def test_rejects_naive_event_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "event_at must be timezone-aware"):
            ReleaseTarget(
                tracked_event_id="tracked-event-1",
                tracked_instrument_id="tracked-instrument-1",
                instrument="ADSK",
                market="NASDAQ",
                event_at=datetime(2026, 8, 27, 20, 0),
                event_date=date(2026, 8, 27),
                source=MarketEventSource.CALENDAR,
                kind=MarketEventKind.EARNINGS,
            )

    def test_rejects_blank_canonical_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "tracked_event_id must not be blank"):
            ReleaseTarget(
                tracked_event_id=" ",
                tracked_instrument_id="tracked-instrument-1",
                instrument="ADSK",
                market="NASDAQ",
                event_at=datetime(2026, 8, 27, 20, 0, tzinfo=UTC),
                event_date=date(2026, 8, 27),
                source=MarketEventSource.CALENDAR,
                kind=MarketEventKind.EARNINGS,
            )


if __name__ == "__main__":
    unittest.main()
