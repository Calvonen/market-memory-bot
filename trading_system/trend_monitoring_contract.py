from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite


TREND_CANDLE_INTERVAL_MINUTES = 15
TREND_FAST_EMA_PERIOD = 50
TREND_SLOW_EMA_PERIOD = 200
TREND_SLOPE_LOOKBACK_BARS = 4
TREND_CONFIRMATION_BARS = 3
TREND_MIN_COMPLETED_BARS = TREND_SLOW_EMA_PERIOD + TREND_SLOPE_LOOKBACK_BARS


class TrendState(str, Enum):
    """Canonical descriptive state for an enabled Trend tracking profile."""

    UNKNOWN = "unknown"
    NEUTRAL = "neutral"
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass(frozen=True)
class TrendEvaluationInput:
    """Indicator snapshot for one *completed* canonical 15-minute candle.

    The future worker owns market-data acquisition and indicator calculation.
    This contract deliberately does not fetch data, create tracked events,
    invoke Strategy/Risk, or place trades.
    """

    close: float
    ema_fast: float
    ema_slow: float
    ema_fast_lookback: float
    completed_bars: int
    candle_closed: bool = True
    instrument_active: bool = True
    trend_profile_enabled: bool = True
    etoro_identity_resolved: bool = True

    @property
    def ready(self) -> bool:
        values = (self.close, self.ema_fast, self.ema_slow, self.ema_fast_lookback)
        return (
            self.instrument_active
            and self.trend_profile_enabled
            and self.etoro_identity_resolved
            and self.candle_closed
            and self.completed_bars >= TREND_MIN_COMPLETED_BARS
            and all(isfinite(value) and value > 0 for value in values)
        )


@dataclass(frozen=True)
class TrendObservation:
    """One deterministic observation emitted for a completed candle."""

    candidate_state: TrendState
    ready: bool
    reason: str


@dataclass(frozen=True)
class TrendTransition:
    """Result of applying confirmation to a candidate observation."""

    state: TrendState
    pending_candidate: TrendState | None
    pending_count: int
    changed: bool


def evaluate_trend(snapshot: TrendEvaluationInput) -> TrendObservation:
    """Classify trend only when the canonical data prerequisites are satisfied.

    Bullish requires price above fast EMA, fast EMA above slow EMA, and a rising
    fast EMA over four completed bars. Bearish is the exact inverse. Everything
    else is neutral. Missing/stale/incomplete prerequisites fail closed to
    UNKNOWN rather than guessing a direction.
    """

    if not snapshot.ready:
        return TrendObservation(
            candidate_state=TrendState.UNKNOWN,
            ready=False,
            reason="trend_prerequisites_not_ready",
        )

    fast_rising = snapshot.ema_fast > snapshot.ema_fast_lookback
    fast_falling = snapshot.ema_fast < snapshot.ema_fast_lookback

    if snapshot.close > snapshot.ema_fast > snapshot.ema_slow and fast_rising:
        return TrendObservation(
            candidate_state=TrendState.BULLISH,
            ready=True,
            reason="price_above_rising_ema50_above_ema200",
        )

    if snapshot.close < snapshot.ema_fast < snapshot.ema_slow and fast_falling:
        return TrendObservation(
            candidate_state=TrendState.BEARISH,
            ready=True,
            reason="price_below_falling_ema50_below_ema200",
        )

    return TrendObservation(
        candidate_state=TrendState.NEUTRAL,
        ready=True,
        reason="trend_alignment_not_confirmed",
    )


def apply_trend_confirmation(
    *,
    current_state: TrendState,
    observation: TrendObservation,
    pending_candidate: TrendState | None = None,
    pending_count: int = 0,
) -> TrendTransition:
    """Require three consecutive completed-candle observations before a change.

    UNKNOWN observations never advance or reset a known state; they clear the
    pending transition because the evidence chain is no longer continuous.
    A candidate matching the current state also clears pending confirmation.
    """

    current_state = TrendState(current_state)
    if pending_candidate is not None:
        pending_candidate = TrendState(pending_candidate)
    if pending_count < 0:
        raise ValueError("pending_count must be non-negative")

    candidate = observation.candidate_state
    if not observation.ready or candidate is TrendState.UNKNOWN:
        return TrendTransition(
            state=current_state,
            pending_candidate=None,
            pending_count=0,
            changed=False,
        )

    if candidate is current_state:
        return TrendTransition(
            state=current_state,
            pending_candidate=None,
            pending_count=0,
            changed=False,
        )

    next_count = pending_count + 1 if pending_candidate is candidate else 1
    if next_count < TREND_CONFIRMATION_BARS:
        return TrendTransition(
            state=current_state,
            pending_candidate=candidate,
            pending_count=next_count,
            changed=False,
        )

    return TrendTransition(
        state=candidate,
        pending_candidate=None,
        pending_count=0,
        changed=True,
    )
