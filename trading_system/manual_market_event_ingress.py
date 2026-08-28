from __future__ import annotations

from datetime import datetime

from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.market_event_ingress import register_market_event
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
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
