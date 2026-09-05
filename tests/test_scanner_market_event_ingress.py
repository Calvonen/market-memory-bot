from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from trading_system.market_event import MarketEventKind, MarketEventSource
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.scanner_market_event_ingress import register_scanner_market_event
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    etoro_instrument_id=123,
    etoro_symbol="ABC.L",
    etoro_display_name="ABC plc",
)
EVENT_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class _UnusedRuntime:
    def observe_event(self, **kwargs):
        raise AssertionError("scanner ingress must not observe events")


def _monitor() -> RegisteredMarketEventMonitor:
    return RegisteredMarketEventMonitor(_UnusedRuntime())


class ScannerMarketEventIngressTests(unittest.TestCase):
    def test_creates_scanner_event_from_resolved_tracked_identity_and_registers_it(self) -> None:
        monitor = _monitor()

        event = register_scanner_market_event(
            monitor,
            TRACKED,
            event_id="scanner-1",
            event_at=EVENT_AT,
            kind=MarketEventKind.CUSTOM,
            title="Scanner candidate",
        )

        self.assertEqual(event.event_id, "scanner-1")
        self.assertEqual(event.tracked_instrument_id, TRACKED.tracked_instrument_id)
        self.assertEqual(event.instrument, TRACKED.instrument)
        self.assertEqual(event.market, TRACKED.market)
        self.assertEqual(event.event_at, EVENT_AT)
        self.assertEqual(event.source, MarketEventSource.SCANNER)
        self.assertEqual(event.kind, MarketEventKind.CUSTOM)
        self.assertEqual(event.title, "Scanner candidate")
        self.assertTrue(monitor.unregister("scanner-1"))

    def test_scanner_kind_is_explicit_and_preserved(self) -> None:
        monitor = _monitor()

        event = register_scanner_market_event(
            monitor,
            TRACKED,
            event_id="scanner-news",
            event_at=EVENT_AT,
            kind=MarketEventKind.NEWS,
        )

        self.assertEqual(event.kind, MarketEventKind.NEWS)
        self.assertEqual(event.source, MarketEventSource.SCANNER)

    def test_same_scanner_event_is_idempotent(self) -> None:
        monitor = _monitor()

        first = register_scanner_market_event(
            monitor,
            TRACKED,
            event_id="scanner-1",
            event_at=EVENT_AT,
            title="Same event",
        )
        second = register_scanner_market_event(
            monitor,
            TRACKED,
            event_id="scanner-1",
            event_at=EVENT_AT,
            title="Same event",
        )

        self.assertEqual(second, first)
        self.assertTrue(monitor.unregister("scanner-1"))
        self.assertFalse(monitor.unregister("scanner-1"))

    def test_reusing_scanner_event_id_for_different_event_fails_closed(self) -> None:
        monitor = _monitor()
        register_scanner_market_event(
            monitor,
            TRACKED,
            event_id="scanner-1",
            event_at=EVENT_AT,
        )

        with self.assertRaisesRegex(ValueError, "market event registration changed"):
            register_scanner_market_event(
                monitor,
                TRACKED,
                event_id="scanner-1",
                event_at=EVENT_AT + timedelta(minutes=1),
            )

    def test_market_event_validation_still_applies(self) -> None:
        monitor = _monitor()

        with self.assertRaisesRegex(ValueError, "event_at must be timezone-aware"):
            register_scanner_market_event(
                monitor,
                TRACKED,
                event_id="scanner-1",
                event_at=datetime(2026, 9, 5, 12, 0),
            )


if __name__ == "__main__":
    unittest.main()
