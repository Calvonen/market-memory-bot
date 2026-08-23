from __future__ import annotations

from datetime import datetime

from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


def register_release_market_event(
    monitor: RegisteredMarketEventMonitor,
    tracked: TrackedEtoroInstrument,
    *,
    event_id: str,
    event_at: datetime,
    kind: MarketEventKind,
    title: str = "",
) -> MarketEvent:
    """Create and register one externally supplied company-release event.

    Instrument identity is taken only from the already-resolved tracked instrument;
    callers do not provide duplicate symbol/market fields that could drift from the
    live stream identity. Release kind is explicit because company releases may
    represent earnings, guidance, trading updates, dividends, acquisitions, or
    other existing ``MarketEventKind`` values.

    Validation and event-id reuse rules remain delegated to ``MarketEvent`` and
    ``RegisteredMarketEventMonitor``. This ingress does not fetch or discover
    releases, persist data, observe reactions, resolve brokers, or trade.
    """
    event = MarketEvent(
        event_id=event_id,
        tracked_instrument_id=tracked.tracked_instrument_id,
        instrument=tracked.instrument,
        market=tracked.market,
        event_at=event_at,
        source=MarketEventSource.RELEASE,
        kind=kind,
        title=title,
    )
    monitor.register(event, tracked)
    return event
