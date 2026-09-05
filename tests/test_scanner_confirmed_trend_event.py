from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_system.market_event import MarketEventSource
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.scanner_confirmed_trend_event import register_confirmed_scanner_trend_event
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.trend_monitoring_contract import TrendState, TrendTransition


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    etoro_instrument_id=123,
    etoro_symbol="ABC.L",
    etoro_display_name="ABC plc",
)
CANDLE_AT = datetime(2026, 9, 5, 12, 15, tzinfo=UTC)


class _UnusedRuntime:
    def observe_event(self, **kwargs):
        raise AssertionError("scanner trend promotion must not observe events")


def _monitor() -> RegisteredMarketEventMonitor:
    return RegisteredMarketEventMonitor(_UnusedRuntime())


def _transition(*, state: TrendState, changed: bool) -> TrendTransition:
    return TrendTransition(
        state=state,
        pending_candidate=None,
        pending_count=0,
        pending_last_candle_at=None,
        last_processed_candle_at=CANDLE_AT,
        changed=changed,
    )


class ScannerConfirmedTrendEventTests(unittest.TestCase):
    def test_confirmed_bullish_change_registers_scanner_event(self) -> None:
        monitor = _monitor()

        event = register_confirmed_scanner_trend_event(
            monitor,
            TRACKED,
            _transition(state=TrendState.BULLISH, changed=True),
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.source, MarketEventSource.SCANNER)
        self.assertEqual(event.event_at, CANDLE_AT)
        self.assertEqual(event.title, "Confirmed bullish trend")
        self.assertIn("tracked-1", event.event_id)
        self.assertIn("bullish", event.event_id)

    def test_confirmed_bearish_change_registers_scanner_event(self) -> None:
        event = register_confirmed_scanner_trend_event(
            _monitor(),
            TRACKED,
            _transition(state=TrendState.BEARISH, changed=True),
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.title, "Confirmed bearish trend")

    def test_unchanged_or_pending_state_creates_no_event(self) -> None:
        for state in (TrendState.BULLISH, TrendState.BEARISH, TrendState.NEUTRAL):
            with self.subTest(state=state):
                event = register_confirmed_scanner_trend_event(
                    _monitor(),
                    TRACKED,
                    _transition(state=state, changed=False),
                )
                self.assertIsNone(event)

    def test_neutral_change_creates_no_event(self) -> None:
        event = register_confirmed_scanner_trend_event(
            _monitor(),
            TRACKED,
            _transition(state=TrendState.NEUTRAL, changed=True),
        )

        self.assertIsNone(event)

    def test_same_confirmed_transition_is_idempotent(self) -> None:
        monitor = _monitor()
        transition = _transition(state=TrendState.BULLISH, changed=True)

        first = register_confirmed_scanner_trend_event(monitor, TRACKED, transition)
        second = register_confirmed_scanner_trend_event(monitor, TRACKED, transition)

        self.assertEqual(second, first)

    def test_changed_direction_without_candle_identity_fails_closed(self) -> None:
        transition = TrendTransition(
            state=TrendState.BULLISH,
            pending_candidate=None,
            pending_count=0,
            pending_last_candle_at=None,
            last_processed_candle_at=None,
            changed=True,
        )

        with self.assertRaisesRegex(RuntimeError, "missing candle identity"):
            register_confirmed_scanner_trend_event(_monitor(), TRACKED, transition)


if __name__ == "__main__":
    unittest.main()
