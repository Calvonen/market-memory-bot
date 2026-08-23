from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from trading_system.calendar_market_event_ingress import register_calendar_market_event
from trading_system.market_event import MarketEventKind, MarketEventSource
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    etoro_instrument_id=123,
    etoro_symbol="ABC.L",
    etoro_display_name="ABC plc",
)
EVENT_AT = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)


class _UnusedRuntime:
    def observe_event(self, **kwargs):
        raise AssertionError("calendar ingress must not observe events")


def _monitor() -> RegisteredMarketEventMonitor:
    return RegisteredMarketEventMonitor(_UnusedRuntime())


class CalendarMarketEventIngressTests(unittest.TestCase):
    def test_creates_calendar_event_from_resolved_tracked_identity_and_registers_it(self) -> None:
        monitor = _monitor()

        event = register_calendar_market_event(
            monitor,
            TRACKED,
            event_id="calendar-1",
            event_at=EVENT_AT,
            kind=MarketEventKind.EARNINGS,
            title="Interim results",
        )

        self.assertEqual(event.event_id, "calendar-1")
        self.assertEqual(event.tracked_instrument_id, TRACKED.tracked_instrument_id)
        self.assertEqual(event.instrument, TRACKED.instrument)
        self.assertEqual(event.market, TRACKED.market)
        self.assertEqual(event.event_at, EVENT_AT)
        self.assertEqual(event.source, MarketEventSource.CALENDAR)
        self.assertEqual(event.kind, MarketEventKind.EARNINGS)
        self.assertEqual(event.title, "Interim results")
        self.assertTrue(monitor.unregister("calendar-1"))

    def test_calendar_kind_is_explicit_and_preserved(self) -> None:
        monitor = _monitor()

        event = register_calendar_market_event(
            monitor,
            TRACKED,
            event_id="calendar-dividend",
            event_at=EVENT_AT,
            kind=MarketEventKind.DIVIDEND,
        )

        self.assertEqual(event.kind, MarketEventKind.DIVIDEND)
        self.assertEqual(event.source, MarketEventSource.CALENDAR)

    def test_same_calendar_event_is_idempotent(self) -> None:
        monitor = _monitor()

        first = register_calendar_market_event(
            monitor,
            TRACKED,
            event_id="calendar-1",
            event_at=EVENT_AT,
            kind=MarketEventKind.EARNINGS,
            title="Same event",
        )
        second = register_calendar_market_event(
            monitor,
            TRACKED,
            event_id="calendar-1",
            event_at=EVENT_AT,
            kind=MarketEventKind.EARNINGS,
            title="Same event",
        )

        self.assertEqual(second, first)
        self.assertTrue(monitor.unregister("calendar-1"))
        self.assertFalse(monitor.unregister("calendar-1"))

    def test_reusing_calendar_event_id_for_different_event_fails_closed(self) -> None:
        monitor = _monitor()
        register_calendar_market_event(
            monitor,
            TRACKED,
            event_id="calendar-1",
            event_at=EVENT_AT,
            kind=MarketEventKind.EARNINGS,
        )

        with self.assertRaisesRegex(ValueError, "market event registration changed"):
            register_calendar_market_event(
                monitor,
                TRACKED,
                event_id="calendar-1",
                event_at=EVENT_AT + timedelta(minutes=1),
                kind=MarketEventKind.EARNINGS,
            )

    def test_market_event_validation_still_applies(self) -> None:
        monitor = _monitor()

        with self.assertRaisesRegex(ValueError, "event_at must be timezone-aware"):
            register_calendar_market_event(
                monitor,
                TRACKED,
                event_id="calendar-1",
                event_at=datetime(2026, 8, 23, 9, 0),
                kind=MarketEventKind.EARNINGS,
            )


if __name__ == "__main__":
    unittest.main()
