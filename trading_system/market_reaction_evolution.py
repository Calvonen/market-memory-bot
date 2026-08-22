from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trading_system.market_reaction import MarketReactionSnapshot, ReactionDirection


class ReactionEvolution(str, Enum):
    INITIAL = "initial"
    EXTENDING = "extending"
    RETRACING = "retracing"
    REVERSAL = "reversal"
    NEUTRALIZED = "neutralized"
    REACTIVATED = "reactivated"
    STABLE = "stable"


@dataclass(frozen=True)
class MarketReactionEvolutionSnapshot:
    """How one reaction snapshot evolved relative to the prior closed candle."""

    snapshot: MarketReactionSnapshot
    evolution: ReactionEvolution
    previous_return_pct: object | None
    delta_pct_points: object | None


@dataclass
class _ReactionSequenceState:
    instrument: str
    market: str
    etoro_instrument_id: int
    reference_price: object
    last_snapshot: MarketReactionSnapshot


class MarketReactionEvolutionTracker:
    """Track deterministic reaction evolution per tracked instrument and interval.

    State is isolated by ``(tracked_instrument_id, interval_minutes)``. The first
    snapshot fixes the instrument identity and reference price for that sequence.
    Later snapshots must have strictly newer candle starts and the same identity
    and reference price; otherwise the tracker fails closed instead of mixing two
    different reaction baselines or histories.

    This layer describes how the observed reaction changes. It does not create
    strategy signals, trade vetoes, risk decisions, or orders.
    """

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], _ReactionSequenceState] = {}

    @staticmethod
    def _classify(
        previous: MarketReactionSnapshot,
        current: MarketReactionSnapshot,
    ) -> ReactionEvolution:
        if current.direction == ReactionDirection.FLAT:
            if previous.direction == ReactionDirection.FLAT:
                return ReactionEvolution.STABLE
            return ReactionEvolution.NEUTRALIZED

        if previous.direction == ReactionDirection.FLAT:
            return ReactionEvolution.REACTIVATED

        if current.direction != previous.direction:
            return ReactionEvolution.REVERSAL

        current_magnitude = abs(current.return_pct)
        previous_magnitude = abs(previous.return_pct)
        if current_magnitude > previous_magnitude:
            return ReactionEvolution.EXTENDING
        if current_magnitude < previous_magnitude:
            return ReactionEvolution.RETRACING
        return ReactionEvolution.STABLE

    def add(self, snapshot: MarketReactionSnapshot) -> MarketReactionEvolutionSnapshot:
        """Consume one closed-candle reaction snapshot and classify its evolution."""
        if not snapshot.return_pct.is_finite():
            raise ValueError("return_pct must be finite")
        if not snapshot.reference_price.is_finite() or snapshot.reference_price <= 0:
            raise ValueError("reference_price must be finite and positive")
        if not snapshot.close_price.is_finite() or snapshot.close_price <= 0:
            raise ValueError("close_price must be finite and positive")
        if snapshot.interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")
        if snapshot.source_minutes < 1 or snapshot.source_minutes > snapshot.interval_minutes:
            raise ValueError("source_minutes must be within the candle interval")

        key = (snapshot.tracked_instrument_id, snapshot.interval_minutes)
        state = self._states.get(key)
        if state is None:
            self._states[key] = _ReactionSequenceState(
                instrument=snapshot.instrument,
                market=snapshot.market,
                etoro_instrument_id=snapshot.etoro_instrument_id,
                reference_price=snapshot.reference_price,
                last_snapshot=snapshot,
            )
            return MarketReactionEvolutionSnapshot(
                snapshot=snapshot,
                evolution=ReactionEvolution.INITIAL,
                previous_return_pct=None,
                delta_pct_points=None,
            )

        if (
            state.instrument != snapshot.instrument
            or state.market != snapshot.market
            or state.etoro_instrument_id != snapshot.etoro_instrument_id
            or state.reference_price != snapshot.reference_price
        ):
            raise ValueError("reaction sequence identity or reference price changed")
        if snapshot.candle_start <= state.last_snapshot.candle_start:
            raise ValueError("reaction snapshots must be strictly chronological")

        previous = state.last_snapshot
        evolution = self._classify(previous, snapshot)
        result = MarketReactionEvolutionSnapshot(
            snapshot=snapshot,
            evolution=evolution,
            previous_return_pct=previous.return_pct,
            delta_pct_points=snapshot.return_pct - previous.return_pct,
        )
        state.last_snapshot = snapshot
        return result
