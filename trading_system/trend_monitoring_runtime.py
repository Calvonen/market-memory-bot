from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, timedelta
from decimal import Decimal

from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.trend_monitoring_contract import (
    TREND_FAST_EMA_PERIOD,
    TREND_MIN_COMPLETED_BARS,
    TREND_SLOW_EMA_PERIOD,
    TrendEvaluationInput,
    TrendObservation,
    TrendState,
    TrendTransition,
    apply_trend_confirmation,
    evaluate_trend,
)


@dataclass(frozen=True)
class TrendRuntimeResult:
    tracked_instrument_id: str
    candle: TrackedMarketCandle
    observation: TrendObservation
    transition: TrendTransition


@dataclass
class _TrendIndicatorState:
    instrument: str
    market: str
    etoro_instrument_id: int
    completed_bars: int = 0
    warmup_closes: list[float] = field(default_factory=list)
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_fast_history: deque[float] = field(default_factory=lambda: deque(maxlen=5))
    last_candle: TrackedMarketCandle | None = None
    state: TrendState = TrendState.UNKNOWN
    pending_candidate: TrendState | None = None
    pending_count: int = 0
    pending_last_candle_at: object | None = None
    last_processed_candle_at: object | None = None


class TrendMonitoringRuntime:
    """Consume canonical closed 15m candles and apply the Trend contract.

    The runtime owns only bounded indicator/confirmation state. It does not read
    Supabase, resolve instruments, open eToro streams, create events, or invoke
    Strategy/Risk/Broker paths. Callers must supply the current prerequisite
    evidence explicitly on every 15-minute candle.
    """

    def __init__(self) -> None:
        self._states: dict[str, _TrendIndicatorState] = {}

    @staticmethod
    def _same_candle(left: TrackedMarketCandle, right: TrackedMarketCandle) -> bool:
        return (
            left.tracked_instrument_id == right.tracked_instrument_id
            and left.instrument == right.instrument
            and left.market == right.market
            and left.etoro_instrument_id == right.etoro_instrument_id
            and left.interval_minutes == right.interval_minutes
            and left.start == right.start
            and left.open == right.open
            and left.high == right.high
            and left.low == right.low
            and left.close == right.close
            and left.source_minutes == right.source_minutes
        )

    def _state_for(self, candle: TrackedMarketCandle) -> _TrendIndicatorState:
        tracked_id = candle.tracked_instrument_id.strip()
        if not tracked_id:
            raise ValueError("tracked_instrument_id is required")
        state = self._states.get(tracked_id)
        if state is None:
            state = _TrendIndicatorState(
                instrument=candle.instrument,
                market=candle.market,
                etoro_instrument_id=candle.etoro_instrument_id,
            )
            self._states[tracked_id] = state
            return state
        if (
            state.instrument != candle.instrument
            or state.market != candle.market
            or state.etoro_instrument_id != candle.etoro_instrument_id
        ):
            raise ValueError("tracked instrument identity changed")
        return state

    @staticmethod
    def _update_indicators(state: _TrendIndicatorState, close: Decimal) -> None:
        value = float(close)
        if value <= 0:
            raise ValueError("trend candle close must be positive")

        state.completed_bars += 1
        if state.completed_bars <= TREND_SLOW_EMA_PERIOD:
            state.warmup_closes.append(value)

        fast_alpha = 2.0 / (TREND_FAST_EMA_PERIOD + 1.0)
        if state.completed_bars == TREND_FAST_EMA_PERIOD:
            state.ema_fast = sum(state.warmup_closes[:TREND_FAST_EMA_PERIOD]) / TREND_FAST_EMA_PERIOD
        elif state.completed_bars > TREND_FAST_EMA_PERIOD:
            assert state.ema_fast is not None
            state.ema_fast = fast_alpha * value + (1.0 - fast_alpha) * state.ema_fast
        if state.ema_fast is not None:
            state.ema_fast_history.append(state.ema_fast)

        slow_alpha = 2.0 / (TREND_SLOW_EMA_PERIOD + 1.0)
        if state.completed_bars == TREND_SLOW_EMA_PERIOD:
            state.ema_slow = sum(state.warmup_closes) / TREND_SLOW_EMA_PERIOD
            state.warmup_closes.clear()
        elif state.completed_bars > TREND_SLOW_EMA_PERIOD:
            assert state.ema_slow is not None
            state.ema_slow = slow_alpha * value + (1.0 - slow_alpha) * state.ema_slow

    @staticmethod
    def _apply(state: _TrendIndicatorState, observation: TrendObservation) -> TrendTransition:
        transition = apply_trend_confirmation(
            current_state=state.state,
            observation=observation,
            pending_candidate=state.pending_candidate,
            pending_count=state.pending_count,
            pending_last_candle_at=state.pending_last_candle_at,
            last_processed_candle_at=state.last_processed_candle_at,
        )
        state.state = transition.state
        state.pending_candidate = transition.pending_candidate
        state.pending_count = transition.pending_count
        state.pending_last_candle_at = transition.pending_last_candle_at
        state.last_processed_candle_at = transition.last_processed_candle_at
        return transition

    def add_candle(
        self,
        candle: TrackedMarketCandle,
        *,
        instrument_active: bool,
        trend_profile_enabled: bool,
        etoro_identity_resolved: bool,
    ) -> TrendRuntimeResult | None:
        if candle.interval_minutes != 15:
            return None

        if candle.start.tzinfo is None or candle.start.utcoffset() is None:
            raise ValueError("trend candle start must be timezone-aware")
        candle_start = candle.start.astimezone(UTC)

        state = self._state_for(candle)
        if state.last_candle is not None:
            last_start = state.last_candle.start.astimezone(UTC)
            if candle_start < last_start:
                raise ValueError("trend candle arrived out of order")
            if candle_start == last_start:
                if self._same_candle(candle, state.last_candle):
                    return None
                raise ValueError("conflicting duplicate trend candle")

        candle_closed_at = candle_start + timedelta(minutes=15)
        complete_window = candle.source_minutes == 15
        if complete_window:
            self._update_indicators(state, candle.close)

        ready_indicators = (
            complete_window
            and state.completed_bars >= TREND_MIN_COMPLETED_BARS
            and state.ema_fast is not None
            and state.ema_slow is not None
            and len(state.ema_fast_history) == 5
        )
        close = float(candle.close)
        observation = evaluate_trend(
            TrendEvaluationInput(
                close=close,
                ema_fast=state.ema_fast if ready_indicators else close,
                ema_slow=state.ema_slow if ready_indicators else close,
                ema_fast_lookback=state.ema_fast_history[0] if ready_indicators else close,
                completed_bars=state.completed_bars,
                candle_closed_at=candle_closed_at,
                candle_closed=complete_window,
                instrument_active=instrument_active,
                trend_profile_enabled=trend_profile_enabled,
                etoro_identity_resolved=etoro_identity_resolved,
            )
        )
        transition = self._apply(state, observation)
        state.last_candle = candle
        return TrendRuntimeResult(
            tracked_instrument_id=candle.tracked_instrument_id,
            candle=candle,
            observation=observation,
            transition=transition,
        )
