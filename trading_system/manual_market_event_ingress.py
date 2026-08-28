from __future__ import annotations

from datetime import date, datetime

from trading_system.canonical_tracked_event_ingress import (
    CanonicalTrackedEventWriteResult,
    SupabaseCanonicalTrackedEventIngress,
)
from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.market_event_ingress import register_market_event
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.tracked_event_repository import TrackedEventTimeStatus
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


def register_manual_market_event(
    monitor: RegisteredMarketEventMonitor,
    tracked: TrackedEtoroInstrument,
    *,
    event_id: str,
    event_at: datetime,
    kind: MarketEventKind = MarketEventKind.CUSTOM,
    title: str = "",
) -> MarketEvent:
    """Create and register one caller-supplied manual market event.

    Instrument identity is taken only from the already-resolved tracked instrument;
    callers do not provide duplicate symbol/market fields that could drift from the
    live stream identity. Validation and event-id reuse rules remain delegated to
    the producer-neutral ``register_market_event`` boundary.

    This ingress does not discover events, persist data, resolve brokers, fetch
    market data, or make trading decisions.
    """
    return register_market_event(
        monitor,
        tracked,
        event_id=event_id,
        event_at=event_at,
        source=MarketEventSource.MANUAL,
        kind=kind,
        title=title,
    )


def persist_manual_market_event(
    ingress: SupabaseCanonicalTrackedEventIngress,
    tracked: TrackedEtoroInstrument,
    *,
    company_name: str,
    external_key: str,
    event_at: datetime,
    event_date: date,
    event_time_status: TrackedEventTimeStatus,
    actor: str,
    kind: MarketEventKind = MarketEventKind.CUSTOM,
    title: str = "",
) -> CanonicalTrackedEventWriteResult:
    """Persist a manual producer event through the canonical tracked-event writer.

    Manual discovery supplies event metadata only. The instrument and market are
    always taken from the already-resolved tracked instrument, and ``event_date``
    is explicit so calendar ownership or UTC-date inference is never required.
    """
    return ingress.register_for_tracked_instrument(
        tracked,
        company_name=company_name,
        source=MarketEventSource.MANUAL.value,
        external_key=external_key,
        kind=kind.value,
        title=title,
        event_at=event_at,
        event_date=event_date,
        event_time_status=event_time_status,
        actor=actor,
    )
