from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_system.event_market_reaction import (
    EventMarketReaction,
    EventMarketReactionPipeline,
)
from trading_system.event_reaction_reference import (
    EventReactionReferenceSelection,
    select_event_reaction_reference,
)
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


@dataclass(frozen=True)
class EventReactionObservation:
    """One deterministic event reaction with its explicit pre-event reference."""

    reference: EventReactionReferenceSelection
    reaction: EventMarketReaction


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


class EventReactionOrchestrator:
    """Compose pre-event reference selection with event-scoped reaction state.

    The caller supplies an explicit event timestamp, the resolved tracked identity,
    already-closed candidate reference candles, and one complete post-event candle.
    This layer selects the deterministic pre-event baseline and then delegates the
    reaction/evolution state to ``EventMarketReactionPipeline``.

    No event discovery, market-data fetching, persistence, strategy, risk, broker,
    AI, or trade-veto behavior is introduced here.
    """

    def __init__(self, *, reaction_pipeline: EventMarketReactionPipeline | None = None) -> None:
        self.reaction_pipeline = reaction_pipeline or EventMarketReactionPipeline()

    @staticmethod
    def _validate_reaction_candle(
        *,
        event_at: datetime,
        tracked: TrackedEtoroInstrument,
        candle: TrackedMarketCandle,
    ) -> None:
        if not _is_aware(event_at):
            raise ValueError("event_at must be timezone-aware")
        if (
            candle.tracked_instrument_id != tracked.tracked_instrument_id
            or candle.instrument != tracked.instrument
            or candle.market != tracked.market
            or candle.etoro_instrument_id != tracked.etoro_instrument_id
        ):
            raise ValueError("reaction candle and tracked identity mismatch")
        if candle.interval_minutes <= 0:
            raise ValueError("reaction candle interval must be positive")
        if candle.source_minutes != candle.interval_minutes:
            raise ValueError("reaction candle must be a complete window")
        if not _is_aware(candle.start):
            raise ValueError("reaction candle start must be timezone-aware")

        # Use only a complete candle whose entire window starts at or after the
        # event. This avoids mixing pre-event prices into the first measured
        # reaction when the event occurs in the middle of a candle interval.
        if candle.start < event_at:
            raise ValueError("reaction candle must start at or after event_at")

        candle_end = candle.start + timedelta(minutes=candle.interval_minutes)
        if candle_end <= event_at:
            raise ValueError("reaction candle must end after event_at")

    def add(
        self,
        *,
        event_id: str,
        event_at: datetime,
        tracked: TrackedEtoroInstrument,
        reference_candles: tuple[TrackedMarketCandle, ...],
        reaction_candle: TrackedMarketCandle,
    ) -> EventReactionObservation | None:
        """Select the baseline and analyze one complete post-event candle.

        ``None`` means that no eligible pre-event reference candle was available;
        in that case reaction state is not touched.
        """
        self._validate_reaction_candle(
            event_at=event_at,
            tracked=tracked,
            candle=reaction_candle,
        )
        reference = select_event_reaction_reference(
            event_id=event_id,
            event_at=event_at,
            tracked=tracked,
            candles=reference_candles,
            interval_minutes=reaction_candle.interval_minutes,
        )
        if reference is None:
            return None

        reaction = self.reaction_pipeline.add(
            reaction_candle,
            baseline=reference.baseline,
        )
        return EventReactionObservation(reference=reference, reaction=reaction)
