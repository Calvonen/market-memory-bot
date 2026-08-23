from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trading_system.event_reaction_orchestration import EventReactionObservation
from trading_system.market_event import MarketEvent
from trading_system.market_event_reaction import MarketEventReactionBridge
from trading_system.reaction_monitoring_profile import (
    DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    ReactionMonitoringProfile,
)
from trading_system.tracked_candle_pipeline import TrackedCandlePipeline, TrackedMarketCandle
from trading_system.tracked_etoro_live import TrackedEtoroMarketUpdate
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.tracked_one_minute_history import TrackedOneMinuteHistory


class ClosedCandlePipeline(Protocol):
    def add(self, item: TrackedEtoroMarketUpdate) -> tuple[TrackedMarketCandle, ...]: ...


class TrackedEventReactionRuntime:
    """Wire the continuous tracked candle stream to event reaction analysis.

    Market data is consumed continuously, before any event needs to be known. Every
    newly closed batch is forwarded to the bounded one-minute history so an
    unexpected intraday event can still use a deterministic pre-event reference.
    Event observation remains a separate operation: it reads the already-kept
    history and delegates interval/overlap/reaction rules to
    ``MarketEventReactionBridge``.

    This class does not discover events, persist data, or make trading decisions.
    """

    def __init__(
        self,
        *,
        candle_pipeline: ClosedCandlePipeline | None = None,
        history: TrackedOneMinuteHistory | None = None,
        bridge: MarketEventReactionBridge | None = None,
    ) -> None:
        self.candle_pipeline = candle_pipeline or TrackedCandlePipeline()
        self.history = history or TrackedOneMinuteHistory()
        self.bridge = bridge or MarketEventReactionBridge()

    def add_market_update(
        self,
        item: TrackedEtoroMarketUpdate,
    ) -> tuple[TrackedMarketCandle, ...]:
        """Build closed candles and retain their complete 1m members for references."""
        candles = self.candle_pipeline.add(item)
        self.history.add(candles)
        return candles

    def observe_event(
        self,
        *,
        event: MarketEvent,
        tracked: TrackedEtoroInstrument,
        reaction_candles: tuple[TrackedMarketCandle, ...],
        observed_at: datetime,
        profile: ReactionMonitoringProfile = DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    ) -> EventReactionObservation | None:
        """Analyze one already-closed batch using the retained 1m reference history."""
        reference_candles = self.history.candles_for(tracked.tracked_instrument_id)
        return self.bridge.add_for_observation(
            event=event,
            tracked=tracked,
            reference_candles=reference_candles,
            reaction_candles=reaction_candles,
            observed_at=observed_at,
            profile=profile,
        )
