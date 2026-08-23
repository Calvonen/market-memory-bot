from __future__ import annotations

from trading_system.event_reaction_orchestration import (
    EventReactionObservation,
    EventReactionOrchestrator,
)
from trading_system.market_event import MarketEvent
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
    def _validate_identity(event: MarketEvent, tracked: TrackedEtoroInstrument) -> None:
        if (
            event.tracked_instrument_id != tracked.tracked_instrument_id
            or event.instrument != tracked.instrument
            or event.market != tracked.market
        ):
            raise ValueError("market event and tracked identity mismatch")

    def add(
        self,
        *,
        event: MarketEvent,
        tracked: TrackedEtoroInstrument,
        reference_candles: tuple[TrackedMarketCandle, ...],
        reaction_candle: TrackedMarketCandle,
    ) -> EventReactionObservation | None:
        """Analyze one closed post-event candle for a generic market event."""
        self._validate_identity(event, tracked)
        return self.orchestrator.add(
            event_id=event.event_id,
            event_at=event.event_at,
            tracked=tracked,
            reference_candles=reference_candles,
            reaction_candle=reaction_candle,
        )
