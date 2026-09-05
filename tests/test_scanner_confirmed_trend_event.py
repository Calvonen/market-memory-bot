from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.market_event import MarketEventSource
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.scanner_confirmed_trend_event import register_confirmed_scanner_trend_event
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.trend_monitoring_contract import (
    TrendObservation,
    TrendState,
    TrendTransition,
)
from trading_system.trend_monitoring_runtime import TrendRuntimeResult


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


def _transition(
    *,
    state: TrendState,
    changed: bool,
    candle_at: datetime | None = CANDLE_AT,
) -> TrendTransition:
    return TrendTransition(
        state=state,
        pending_candidate=None,
        pending_count=0,
        pending_last_candle_at=None,
        last_processed_candle_at=candle_at,
        changed=changed,
    )


def _result(
    *,
    state: TrendState,
    changed: bool,
    tracked_instrument_id: str = "tracked-1",
    candle_tracked_instrument_id: str | None = None,
    instrument: str = "ABC",
    market: str = "LSE",
    etoro_instrument_id: int = 123,
    candle_at: datetime | None = CANDLE_AT,
) -> TrendRuntimeResult:
    candle_tracked_id = candle_tracked_instrument_id or tracked_instrument_id
    candle = TrackedMarketCandle(
        tracked_instrument_id=candle_tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_instrument_id,
        interval_minutes=15,
        start=CANDLE_AT.replace(minute=0),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        source_minutes=15,
    )
    observation = TrendObservation(
        candidate_state=state,
        ready=True,
        reason="test",
        candle_closed_at=CANDLE_AT,
    )
    return TrendRuntimeResult(
        tracked_instrument_id=tracked_instrument_id,
        candle=candle,
        observation=observation,
        transition=_transition(state=state, changed=changed, candle_at=candle_at),
    )


class ScannerConfirmedTrendEventTests(unittest.TestCase):
    def test_confirmed_bullish_change_registers_scanner_event(self) -> None:
        monitor = _monitor()

        event = register_confirmed_scanner_trend_event(
            monitor,
            TRACKED,
            _result(state=TrendState.BULLISH, changed=True),
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
            _result(state=TrendState.BEARISH, changed=True),
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
                    _result(state=state, changed=False),
                )
                self.assertIsNone(event)

    def test_neutral_change_creates_no_event(self) -> None:
        event = register_confirmed_scanner_trend_event(
            _monitor(),
            TRACKED,
            _result(state=TrendState.NEUTRAL, changed=True),
        )

        self.assertIsNone(event)

    def test_same_confirmed_transition_is_idempotent(self) -> None:
        monitor = _monitor()
        result = _result(state=TrendState.BULLISH, changed=True)

        first = register_confirmed_scanner_trend_event(monitor, TRACKED, result)
        second = register_confirmed_scanner_trend_event(monitor, TRACKED, result)

        self.assertEqual(second, first)

    def test_changed_direction_without_candle_identity_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "missing candle identity"):
            register_confirmed_scanner_trend_event(
                _monitor(),
                TRACKED,
                _result(state=TrendState.BULLISH, changed=True, candle_at=None),
            )

    def test_changed_direction_with_naive_candle_identity_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "timezone-aware"):
            register_confirmed_scanner_trend_event(
                _monitor(),
                TRACKED,
                _result(
                    state=TrendState.BULLISH,
                    changed=True,
                    candle_at=datetime(2026, 9, 5, 12, 15),
                ),
            )

    def test_runtime_result_for_different_tracked_instrument_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "does not match tracked instrument identity"):
            register_confirmed_scanner_trend_event(
                _monitor(),
                TRACKED,
                _result(
                    state=TrendState.BULLISH,
                    changed=True,
                    tracked_instrument_id="tracked-2",
                    instrument="XYZ",
                    etoro_instrument_id=456,
                ),
            )

    def test_inconsistent_runtime_result_identity_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "tracked identity is inconsistent"):
            register_confirmed_scanner_trend_event(
                _monitor(),
                TRACKED,
                _result(
                    state=TrendState.BULLISH,
                    changed=True,
                    candle_tracked_instrument_id="tracked-other",
                ),
            )


if __name__ == "__main__":
    unittest.main()
