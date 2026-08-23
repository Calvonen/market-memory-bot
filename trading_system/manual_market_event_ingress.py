from __future__ import annotations

from datetime import datetime

from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
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
    ``MarketEvent`` and ``RegisteredMarketEventMonitor``.

    This ingress does not discover events, persist data, resolve brokers, fetch
    market data, or make trading decisions.
    """
    event = MarketEvent(
        event_id=event_id,
        tracked_instrument_id=tracked.tracked_instrument_id,
        instrument=tracked.instrument,
        market=tracked.market,
        event_at=event_at,
        source=MarketEventSource.MANUAL,
        kind=kind,
        title=title,
    )
    monitor.register(event, tracked)
    return event
