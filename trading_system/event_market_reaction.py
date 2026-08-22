from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_market_reaction_pipeline import (
    TrackedMarketReaction,
    TrackedMarketReactionPipeline,
)


@dataclass(frozen=True)
class EventMarketReactionBaseline:
    """Explicit event-scoped reference price for one tracked instrument."""

    event_id: str
    tracked_instrument_id: str
    instrument: str
    market: str
    etoro_instrument_id: int
    reference_price: Decimal


@dataclass(frozen=True)
class EventMarketReaction:
    """One tracked market reaction tied to an explicit event baseline."""

    event_id: str
    baseline: EventMarketReactionBaseline
    tracked_reaction: TrackedMarketReaction


@dataclass
class _EventReactionState:
    baseline: EventMarketReactionBaseline
    pipeline: TrackedMarketReactionPipeline


class EventMarketReactionPipeline:
    """Run deterministic reaction sequences independently for each event.

    The caller owns baseline discovery. This layer only validates that the
    supplied baseline belongs to the same tracked instrument as the candle and
    keeps a separate ``TrackedMarketReactionPipeline`` per ``event_id``.

    Once an event sequence has started, its full baseline identity and reference
    price are locked. Different events for the same instrument therefore start
    independent reaction histories even if they happen to use the same price.

    No strategy, risk, broker, persistence, event discovery, or trade-veto
    behavior is introduced here.
    """

    def __init__(self) -> None:
        self._states: dict[str, _EventReactionState] = {}

    @staticmethod
    def _validate_baseline(
        candle: TrackedMarketCandle,
        baseline: EventMarketReactionBaseline,
    ) -> None:
        if not baseline.event_id.strip():
            raise ValueError("event_id must not be blank")
        if not baseline.reference_price.is_finite() or baseline.reference_price <= 0:
            raise ValueError("reference_price must be finite and positive")
        if (
            baseline.tracked_instrument_id != candle.tracked_instrument_id
            or baseline.instrument != candle.instrument
            or baseline.market != candle.market
            or baseline.etoro_instrument_id != candle.etoro_instrument_id
        ):
            raise ValueError("event baseline and candle identity mismatch")

    def add(
        self,
        candle: TrackedMarketCandle,
        *,
        baseline: EventMarketReactionBaseline,
    ) -> EventMarketReaction:
        """Analyze one closed candle inside its event-scoped reaction sequence."""
        self._validate_baseline(candle, baseline)

        state = self._states.get(baseline.event_id)
        if state is not None:
            if state.baseline != baseline:
                raise ValueError("event baseline changed")
            tracked_reaction = state.pipeline.add(
                candle,
                reference_price=baseline.reference_price,
            )
        else:
            pipeline = TrackedMarketReactionPipeline()
            tracked_reaction = pipeline.add(
                candle,
                reference_price=baseline.reference_price,
            )
            self._states[baseline.event_id] = _EventReactionState(
                baseline=baseline,
                pipeline=pipeline,
            )

        return EventMarketReaction(
            event_id=baseline.event_id,
            baseline=baseline,
            tracked_reaction=tracked_reaction,
        )
