from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from trading_system.manual_market_event_ingress import register_manual_market_event
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
EVENT_AT = datetime(2026, 8, 23, 7, 30, tzinfo=UTC)


class ManualMarketEventIngressTests(unittest.TestCase):
    def test_creates_manual_event_from_resolved_tracked_identity_and_registers_it(self) -> None:
        monitor = RegisteredMarketEventMonitor()

        event = register_manual_market_event(
            monitor,
            TRACKED,
            event_id="manual-1",
            event_at=EVENT_AT,
            kind=MarketEventKind.NEWS,
            title="Unexpected contract announcement",
        )

        self.assertEqual(event.event_id, "manual-1")
        self.assertEqual(event.tracked_instrument_id, TRACKED.tracked_instrument_id)
        self.assertEqual(event.instrument, TRACKED.instrument)
        self.assertEqual(event.market, TRACKED.market)
        self.assertEqual(event.event_at, EVENT_AT)
        self.assertEqual(event.source, MarketEventSource.MANUAL)
        self.assertEqual(event.kind, MarketEventKind.NEWS)
        self.assertEqual(event.title, "Unexpected contract announcement")

        self.assertEqual(monitor.registration_for("manual-1").event, event)
        self.assertEqual(monitor.registration_for("manual-1").tracked, TRACKED)

    def test_default_kind_is_custom(self) -> None:
        monitor = RegisteredMarketEventMonitor()

        event = register_manual_market_event(
            monitor,
            TRACKED,
            event_id="manual-custom",
            event_at=EVENT_AT,
        )

        self.assertEqual(event.kind, MarketEventKind.CUSTOM)
        self.assertEqual(event.source, MarketEventSource.MANUAL)

    def test_same_manual_event_is_idempotent(self) -> None:
        monitor = RegisteredMarketEventMonitor()

        first = register_manual_market_event(
            monitor,
            TRACKED,
            event_id="manual-1",
            event_at=EVENT_AT,
            title="Same event",
        )
        second = register_manual_market_event(
            monitor,
            TRACKED,
            event_id="manual-1",
            event_at=EVENT_AT,
            title="Same event",
        )

        self.assertEqual(second, first)
        self.assertEqual(monitor.registration_for("manual-1").event, first)

    def test_reusing_manual_event_id_for_different_event_fails_closed(self) -> None:
        monitor = RegisteredMarketEventMonitor()
        register_manual_market_event(
            monitor,
            TRACKED,
            event_id="manual-1",
            event_at=EVENT_AT,
        )

        with self.assertRaisesRegex(ValueError, "market event registration changed"):
            register_manual_market_event(
                monitor,
                TRACKED,
                event_id="manual-1",
                event_at=EVENT_AT + timedelta(minutes=1),
            )

    def test_market_event_validation_still_applies(self) -> None:
        monitor = RegisteredMarketEventMonitor()

        with self.assertRaisesRegex(ValueError, "event_at must be timezone-aware"):
            register_manual_market_event(
                monitor,
                TRACKED,
                event_id="manual-1",
                event_at=datetime(2026, 8, 23, 7, 30),
            )


if __name__ == "__main__":
    unittest.main()
