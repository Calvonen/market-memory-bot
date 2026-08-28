from __future__ import annotations

from datetime import datetime

from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


def register_market_event(
    monitor: RegisteredMarketEventMonitor,
    tracked: TrackedEtoroInstrument,
    *,
    event_id: str,
    event_at: datetime,
    source: MarketEventSource,
    kind: MarketEventKind,
    title: str = "",
) -> MarketEvent:
    """Register one producer-neutral event for an already-resolved instrument.

    Calendar, release, manual and news producers all enter the same canonical
    event path through this function. Broker/instrument identity comes only from
    the resolved tracked instrument so producer metadata cannot create a second
    identity or a source-specific reaction path.

    This boundary intentionally does not discover events, resolve instruments,
    persist data, fetch releases or market data, observe reactions, or make
    trading decisions.
    """
    event = MarketEvent(
        event_id=event_id,
        tracked_instrument_id=tracked.tracked_instrument_id,
        instrument=tracked.instrument,
        market=tracked.market,
        event_at=event_at,
        source=source,
        kind=kind,
        title=title,
    )
    monitor.register(event, tracked)
    return event
