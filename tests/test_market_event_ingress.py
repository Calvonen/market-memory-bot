from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from trading_system.market_event import MarketEventKind, MarketEventSource
from trading_system.market_event_ingress import register_market_event
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
        raise AssertionError("canonical ingress must not observe events")


def _monitor() -> RegisteredMarketEventMonitor:
    return RegisteredMarketEventMonitor(_UnusedRuntime())


class MarketEventIngressTests(unittest.TestCase):
    def test_preserves_explicit_producer_source_on_one_canonical_path(self) -> None:
        for source in (
            MarketEventSource.CALENDAR,
            MarketEventSource.RELEASE,
            MarketEventSource.MANUAL,
            MarketEventSource.NEWS,
        ):
            with self.subTest(source=source):
                monitor = _monitor()
                event = register_market_event(
                    monitor,
                    TRACKED,
                    event_id=f"{source.value}-1",
                    event_at=EVENT_AT,
                    source=source,
                    kind=MarketEventKind.EARNINGS,
                    title="Results",
                )
                self.assertEqual(event.source, source)
                self.assertEqual(event.tracked_instrument_id, TRACKED.tracked_instrument_id)
                self.assertEqual(event.instrument, TRACKED.instrument)
                self.assertEqual(event.market, TRACKED.market)

    def test_same_event_is_idempotent(self) -> None:
        monitor = _monitor()
        first = register_market_event(
            monitor,
            TRACKED,
            event_id="manual-1",
            event_at=EVENT_AT,
            source=MarketEventSource.MANUAL,
            kind=MarketEventKind.EARNINGS,
        )
        second = register_market_event(
            monitor,
            TRACKED,
            event_id="manual-1",
            event_at=EVENT_AT,
            source=MarketEventSource.MANUAL,
            kind=MarketEventKind.EARNINGS,
        )
        self.assertEqual(second, first)

    def test_conflicting_reuse_fails_closed(self) -> None:
        monitor = _monitor()
        register_market_event(
            monitor,
            TRACKED,
            event_id="manual-1",
            event_at=EVENT_AT,
            source=MarketEventSource.MANUAL,
            kind=MarketEventKind.EARNINGS,
        )
        with self.assertRaisesRegex(ValueError, "market event registration changed"):
            register_market_event(
                monitor,
                TRACKED,
                event_id="manual-1",
                event_at=EVENT_AT + timedelta(minutes=1),
                source=MarketEventSource.MANUAL,
                kind=MarketEventKind.EARNINGS,
            )


if __name__ == "__main__":
    unittest.main()
