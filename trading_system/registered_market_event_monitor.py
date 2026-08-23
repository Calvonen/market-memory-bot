from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_system.event_reaction_orchestration import EventReactionObservation
from trading_system.market_event import MarketEvent
from trading_system.reaction_monitoring_profile import (
    DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    ReactionMonitoringProfile,
)
from trading_system.tracked_event_reaction_live import TrackedRuntimeMarketBatch
from trading_system.tracked_event_reaction_runtime import TrackedEventReactionRuntime
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


@dataclass(frozen=True)
class RegisteredMarketEvent:
    event: MarketEvent
    tracked: TrackedEtoroInstrument


@dataclass(frozen=True)
class RegisteredEventReaction:
    event: MarketEvent
    observation: EventReactionObservation


class RegisteredMarketEventMonitor:
    """Apply explicitly registered market events to one continuous reaction runtime.

    Producers register already-created ``MarketEvent`` objects against the resolved
    tracked identity. Live market batches remain independent from event discovery;
    when a batch arrives, only registrations for that tracked instrument are
    observed through the existing ``TrackedEventReactionRuntime``.

    Registrations have no implicit expiry. The caller owns their lifecycle and can
    remove an event explicitly. This class does not discover events, build candles,
    persist results, or make trading decisions.
    """

    def __init__(self, runtime: TrackedEventReactionRuntime) -> None:
        self.runtime = runtime
        self._events: dict[str, RegisteredMarketEvent] = {}

    @staticmethod
    def _canonical_market(value: str) -> str:
        return " ".join(value.strip().split()).upper()

    @classmethod
    def _validate_registration(
        cls,
        event: MarketEvent,
        tracked: TrackedEtoroInstrument,
    ) -> None:
        if (
            event.tracked_instrument_id != tracked.tracked_instrument_id
            or event.instrument != tracked.instrument
            or cls._canonical_market(event.market) != cls._canonical_market(tracked.market)
        ):
            raise ValueError("market event and tracked identity mismatch")

    def register(self, event: MarketEvent, tracked: TrackedEtoroInstrument) -> None:
        """Register one event idempotently, failing closed on conflicting reuse."""
        self._validate_registration(event, tracked)
        registration = RegisteredMarketEvent(event=event, tracked=tracked)
        existing = self._events.get(event.event_id)
        if existing is None:
            self._events[event.event_id] = registration
        elif existing != registration:
            raise ValueError("market event registration changed")

    def unregister(self, event_id: str) -> bool:
        """Remove one registration; return whether an event was present."""
        normalized = event_id.strip()
        if not normalized:
            raise ValueError("event_id must not be blank")
        return self._events.pop(normalized, None) is not None

    def observe_batch(
        self,
        batch: TrackedRuntimeMarketBatch,
        *,
        observed_at: datetime,
        profile: ReactionMonitoringProfile = DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    ) -> tuple[RegisteredEventReaction, ...]:
        """Observe one closed-candle batch for matching registered events."""
        if not batch.candles:
            return ()

        tracked_id = batch.update.tracked_instrument_id
        results: list[RegisteredEventReaction] = []
        for registration in self._events.values():
            if registration.tracked.tracked_instrument_id != tracked_id:
                continue
            observation = self.runtime.observe_event(
                event=registration.event,
                tracked=registration.tracked,
                reaction_candles=batch.candles,
                observed_at=observed_at,
                profile=profile,
            )
            if observation is not None:
                results.append(
                    RegisteredEventReaction(
                        event=registration.event,
                        observation=observation,
                    )
                )
        return tuple(results)
