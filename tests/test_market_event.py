from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.tracked_instruments import TrackedInstrument


class MarketEventTests(unittest.TestCase):
    def test_normalizes_identity_without_side_effects(self) -> None:
        event = MarketEvent(
            event_id=" event-1 ",
            tracked_instrument_id=" tracked-1 ",
            instrument=" abc ",
            market=" london   stock exchange ",
            event_at=datetime(2026, 8, 23, 7, 5, tzinfo=UTC),
            source=MarketEventSource.RELEASE,
            kind=MarketEventKind.EARNINGS,
            title=" Half-year results ",
        )

        self.assertEqual(event.event_id, "event-1")
        self.assertEqual(event.tracked_instrument_id, "tracked-1")
        self.assertEqual(event.instrument, "ABC")
        self.assertEqual(event.market, "LONDON STOCK EXCHANGE")
        self.assertEqual(event.title, "Half-year results")

    def test_supports_all_current_event_producers(self) -> None:
        self.assertEqual(
            {source.value for source in MarketEventSource},
            {"calendar", "release", "manual", "news"},
        )

    def test_event_kinds_are_generic_not_earnings_only(self) -> None:
        self.assertEqual(
            {kind.value for kind in MarketEventKind},
            {
                "earnings",
                "guidance",
                "trading_update",
                "acquisition",
                "dividend",
                "management_change",
                "news",
                "custom",
            },
        )

    def test_blank_required_identity_fails_closed(self) -> None:
        base = dict(
            event_id="event-1",
            tracked_instrument_id="tracked-1",
            instrument="ABC",
            market="LSE",
            event_at=datetime(2026, 8, 23, 7, 5, tzinfo=UTC),
            source=MarketEventSource.CALENDAR,
            kind=MarketEventKind.EARNINGS,
        )
        for field_name in ("event_id", "tracked_instrument_id", "instrument", "market"):
            values = dict(base)
            values[field_name] = "   "
            with self.subTest(field=field_name):
                with self.assertRaises(ValueError):
                    MarketEvent(**values)

    def test_instrument_canonicalizes_like_tracked_instrument(self) -> None:
        tracked = TrackedInstrument(instrument=" BRK B ")
        event = MarketEvent(
            event_id="event-1",
            tracked_instrument_id="tracked-1",
            instrument=" BRK B ",
            market="LSE",
            event_at=datetime(2026, 8, 23, 7, 5, tzinfo=UTC),
            source=MarketEventSource.CALENDAR,
            kind=MarketEventKind.EARNINGS,
        )

        self.assertEqual(event.instrument, tracked.instrument)

    def test_naive_event_time_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            MarketEvent(
                event_id="event-1",
                tracked_instrument_id="tracked-1",
                instrument="ABC",
                market="LSE",
                event_at=datetime(2026, 8, 23, 7, 5),
                source=MarketEventSource.MANUAL,
                kind=MarketEventKind.CUSTOM,
            )

    def test_raw_source_or_kind_strings_fail_closed(self) -> None:
        base = dict(
            event_id="event-1",
            tracked_instrument_id="tracked-1",
            instrument="ABC",
            market="LSE",
            event_at=datetime(2026, 8, 23, 7, 5, tzinfo=UTC),
        )
        with self.assertRaisesRegex(ValueError, "MarketEventSource"):
            MarketEvent(source="release", kind=MarketEventKind.EARNINGS, **base)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "MarketEventKind"):
            MarketEvent(source=MarketEventSource.RELEASE, kind="earnings", **base)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
