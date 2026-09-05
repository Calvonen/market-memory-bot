from __future__ import annotations

from datetime import datetime

from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.market_event_ingress import register_market_event
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


def register_scanner_market_event(
    monitor: RegisteredMarketEventMonitor,
    tracked: TrackedEtoroInstrument,
    *,
    event_id: str,
    event_at: datetime,
    kind: MarketEventKind = MarketEventKind.CUSTOM,
    title: str = "",
) -> MarketEvent:
    """Register scanner discovery through the canonical market-event ingress.

    The scanner is only a producer of event metadata. Instrument identity comes
    from the already-resolved tracked instrument and all validation, event-id
    reuse, monitoring, and later trading authority remain owned by the existing
    producer-neutral workflow.

    This adapter does not fetch prices, make strategy/risk decisions, create a
    trading task, or submit an order.
    """
    return register_market_event(
        monitor,
        tracked,
        event_id=event_id,
        event_at=event_at,
        source=MarketEventSource.SCANNER,
        kind=kind,
        title=title,
    )
