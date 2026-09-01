from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from math import isfinite


TREND_CANDLE_INTERVAL_MINUTES = 15
TREND_FAST_EMA_PERIOD = 50
TREND_SLOW_EMA_PERIOD = 200
TREND_SLOPE_LOOKBACK_BARS = 4
TREND_CONFIRMATION_BARS = 3
TREND_MIN_COMPLETED_BARS = TREND_SLOW_EMA_PERIOD + TREND_SLOPE_LOOKBACK_BARS
TREND_CANDLE_INTERVAL = timedelta(minutes=TREND_CANDLE_INTERVAL_MINUTES)


class TrendState(str, Enum):
    """Canonical descriptive state for an enabled Trend tracking profile."""

    UNKNOWN = "unknown"
    NEUTRAL = "neutral"
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass(frozen=True)
class TrendEvaluationInput:
    """Indicator snapshot for one canonical 15-minute candle."""

    close: float
    ema_fast: float
    ema_slow: float
    ema_fast_lookback: float
    completed_bars: int
    candle_closed_at: datetime
    candle_closed: bool
    instrument_active: bool
    trend_profile_enabled: bool
    etoro_identity_resolved: bool

    @property
    def ready(self) -> bool:
        values = (self.close, self.ema_fast, self.ema_slow, self.ema_fast_lookback)
        return (
            self.instrument_active
            and self.trend_profile_enabled
            and self.etoro_identity_resolved
            and self.candle_closed
            and self.candle_closed_at.tzinfo is not None
            and self.candle_closed_at.utcoffset() is not None
            and self.completed_bars >= TREND_MIN_COMPLETED_BARS
            and all(isfinite(value) and value > 0 for value in values)
        )


@dataclass(frozen=True)
class TrendObservation:
    """One deterministic observation emitted for one completed candle."""

    candidate_state: TrendState
    ready: bool
    reason: str
    candle_closed_at: datetime


@dataclass(frozen=True)
class TrendTransition:
    """Result of applying confirmation to a candidate observation."""

    state: TrendState
    pending_candidate: TrendState | None
    pending_count: int
    pending_last_candle_at: datetime | None
    last_processed_candle_at: datetime | None
    changed: bool


def evaluate_trend(snapshot: TrendEvaluationInput) -> TrendObservation:
    """Classify trend only when the canonical data prerequisites are satisfied."""

    if not snapshot.ready:
        return TrendObservation(
            candidate_state=TrendState.UNKNOWN,
            ready=False,
            reason="trend_prerequisites_not_ready",
            candle_closed_at=snapshot.candle_closed_at,
        )

    fast_rising = snapshot.ema_fast > snapshot.ema_fast_lookback
    fast_falling = snapshot.ema_fast < snapshot.ema_fast_lookback

    if snapshot.close > snapshot.ema_fast > snapshot.ema_slow and fast_rising:
        return TrendObservation(
            candidate_state=TrendState.BULLISH,
            ready=True,
            reason="price_above_rising_ema50_above_ema200",
            candle_closed_at=snapshot.candle_closed_at,
        )

    if snapshot.close < snapshot.ema_fast < snapshot.ema_slow and fast_falling:
        return TrendObservation(
            candidate_state=TrendState.BEARISH,
            ready=True,
            reason="price_below_falling_ema50_below_ema200",
            candle_closed_at=snapshot.candle_closed_at,
        )

    return TrendObservation(
        candidate_state=TrendState.NEUTRAL,
        ready=True,
        reason="trend_alignment_not_confirmed",
        candle_closed_at=snapshot.candle_closed_at,
    )


def apply_trend_confirmation(
    *,
    current_state: TrendState,
    observation: TrendObservation,
    pending_candidate: TrendState | None = None,
    pending_count: int = 0,
    pending_last_candle_at: datetime | None = None,
    last_processed_candle_at: datetime | None = None,
) -> TrendTransition:
    """Require three distinct consecutive completed candles before a state change.

    Duplicate or out-of-order observations never advance confirmation. UNKNOWN
    clears pending evidence without erasing the last confirmed state. A gap in
    candle cadence starts a fresh confirmation chain from the new candle.
    """

    current_state = TrendState(current_state)
    if pending_candidate is not None:
        pending_candidate = TrendState(pending_candidate)
    if pending_count < 0:
        raise ValueError("pending_count must be non-negative")

    candle_at = observation.candle_closed_at
    if candle_at.tzinfo is None or candle_at.utcoffset() is None:
        raise ValueError("observation candle_closed_at must be timezone-aware")
    if last_processed_candle_at is not None and candle_at <= last_processed_candle_at:
        return TrendTransition(
            state=current_state,
            pending_candidate=pending_candidate,
            pending_count=pending_count,
            pending_last_candle_at=pending_last_candle_at,
            last_processed_candle_at=last_processed_candle_at,
            changed=False,
        )

    candidate = observation.candidate_state
    if not observation.ready or candidate is TrendState.UNKNOWN:
        return TrendTransition(
            state=current_state,
            pending_candidate=None,
            pending_count=0,
            pending_last_candle_at=None,
            last_processed_candle_at=candle_at,
            changed=False,
        )

    if candidate is current_state:
        return TrendTransition(
            state=current_state,
            pending_candidate=None,
            pending_count=0,
            pending_last_candle_at=None,
            last_processed_candle_at=candle_at,
            changed=False,
        )

    consecutive = (
        pending_candidate is candidate
        and pending_last_candle_at is not None
        and candle_at - pending_last_candle_at == TREND_CANDLE_INTERVAL
    )
    next_count = pending_count + 1 if consecutive else 1

    if next_count < TREND_CONFIRMATION_BARS:
        return TrendTransition(
            state=current_state,
            pending_candidate=candidate,
            pending_count=next_count,
            pending_last_candle_at=candle_at,
            last_processed_candle_at=candle_at,
            changed=False,
        )

    return TrendTransition(
        state=candidate,
        pending_candidate=None,
        pending_count=0,
        pending_last_candle_at=None,
        last_processed_candle_at=candle_at,
        changed=True,
    )
