from __future__ import annotations

from datetime import datetime

from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.market_event_ingress import register_market_event
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


def register_calendar_market_event(
    monitor: RegisteredMarketEventMonitor,
    tracked: TrackedEtoroInstrument,
    *,
    event_id: str,
    event_at: datetime,
    kind: MarketEventKind,
    title: str = "",
) -> MarketEvent:
    """Compatibility adapter from calendar discovery into canonical event ingress.

    The calendar is only a producer of event metadata. It does not own a separate
    reaction/runtime path; all identity validation and registration are delegated
    to the producer-neutral ingress.
    """
    return register_market_event(
        monitor,
        tracked,
        event_id=event_id,
        event_at=event_at,
        source=MarketEventSource.CALENDAR,
        kind=kind,
        title=title,
    )
