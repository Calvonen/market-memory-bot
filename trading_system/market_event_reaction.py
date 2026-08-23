from __future__ import annotations

from datetime import datetime

from trading_system.event_reaction_orchestration import (
    EventReactionObservation,
    EventReactionOrchestrator,
)
from trading_system.market_event import MarketEvent
from trading_system.reaction_monitoring_profile import (
    DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    ReactionMonitoringProfile,
)
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


class MarketEventReactionBridge:
    """Feed one generic market event into deterministic reaction analysis.

    The bridge only validates that the generic event belongs to the resolved
    tracked instrument, then delegates baseline selection and reaction/evolution
    state to ``EventReactionOrchestrator``. It does not discover events, fetch
    market data, persist results, or create strategy/trading decisions.
    """

    def __init__(self, *, orchestrator: EventReactionOrchestrator | None = None) -> None:
        self.orchestrator = orchestrator or EventReactionOrchestrator()

    @staticmethod
    def _canonical_market(value: str) -> str:
        """Canonicalize a market string the same way ``MarketEvent`` does.

        ``TrackedEtoroInstrument.market`` can retain its original casing, while
        ``MarketEvent.market`` is normalised to collapsed-whitespace uppercase.
        Comparing raw strings would fail closed on cosmetic case differences
        (e.g. "LSE" vs "Lse") even though the identity is the same; this is an
        exact match on canonical form, not a fuzzy comparison.
        """
        return " ".join(value.strip().split()).upper()

    @classmethod
    def _validate_identity(cls, event: MarketEvent, tracked: TrackedEtoroInstrument) -> None:
        if (
            event.tracked_instrument_id != tracked.tracked_instrument_id
            or event.instrument != tracked.instrument
            or cls._canonical_market(event.market) != cls._canonical_market(tracked.market)
        ):
            raise ValueError("market event and tracked identity mismatch")

    def add(
        self,
        *,
        event: MarketEvent,
        tracked: TrackedEtoroInstrument,
        reference_candles: tuple[TrackedMarketCandle, ...],
        reaction_candle: TrackedMarketCandle,
        reference_interval_minutes: int | None = None,
    ) -> EventReactionObservation | None:
        """Analyze one closed post-event candle for a generic market event."""
        self._validate_identity(event, tracked)
        return self.orchestrator.add(
            event_id=event.event_id,
            event_at=event.event_at,
            tracked=tracked,
            reference_candles=reference_candles,
            reaction_candle=reaction_candle,
            reference_interval_minutes=reference_interval_minutes,
        )

    def add_for_observation(
        self,
        *,
        event: MarketEvent,
        tracked: TrackedEtoroInstrument,
        reference_candles: tuple[TrackedMarketCandle, ...],
        reaction_candles: tuple[TrackedMarketCandle, ...],
        observed_at: datetime,
        profile: ReactionMonitoringProfile = DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    ) -> EventReactionObservation | None:
        """Analyze the active 1m/5m/15m candle for one observation time.

        ``reaction_candles`` is the caller's batch of newly closed candles. The
        monitoring profile chooses which interval is active relative to the
        event timestamp. Inactive intervals are ignored; if the active interval
        did not close in this batch, no reaction observation is emitted.

        The pre-event reference remains fixed to the 1-minute interval while the
        reaction interval changes, so one event keeps one stable baseline across
        the default 1m -> 5m -> 15m monitoring stages.
        """
        self._validate_identity(event, tracked)
        active_interval = profile.interval_for(
            event_at=event.event_at,
            observed_at=observed_at,
        )
        if active_interval is None:
            return None

        active = tuple(
            candle
            for candle in reaction_candles
            if candle.interval_minutes == active_interval
        )
        if not active:
            return None
        if len(active) != 1:
            raise ValueError("ambiguous active reaction candle")

        return self.add(
            event=event,
            tracked=tracked,
            reference_candles=reference_candles,
            reaction_candle=active[0],
            reference_interval_minutes=1,
        )
