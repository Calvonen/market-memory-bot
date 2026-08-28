from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from trading_system.market_event import MarketEventKind, MarketEventSource
from trading_system.tracked_event_repository import TrackedEventTimeStatus
from trading_system.tracked_instruments import _normalise_symbol


@dataclass(frozen=True)
class ReleaseTarget:
    """Producer-neutral identity for release discovery and analysis.

    A release target belongs to the canonical tracked-event workflow. Calendar,
    scanner, manual and other producers may supply metadata, but none of them own
    the release pipeline or define a separate release identity.
    """

    tracked_event_id: str
    tracked_instrument_id: str
    instrument: str
    market: str
    event_at: datetime
    event_date: date
    event_time_status: TrackedEventTimeStatus
    source: MarketEventSource
    kind: MarketEventKind
    title: str = ""

    def __post_init__(self) -> None:
        tracked_event_id = self.tracked_event_id.strip()
        tracked_instrument_id = self.tracked_instrument_id.strip()
        instrument = _normalise_symbol(self.instrument)
        market = " ".join(self.market.strip().split()).upper()
        title = self.title.strip()

        if not tracked_event_id:
            raise ValueError("tracked_event_id must not be blank")
        if not tracked_instrument_id:
            raise ValueError("tracked_instrument_id must not be blank")
        if not instrument:
            raise ValueError("instrument must not be blank")
        if not market:
            raise ValueError("market must not be blank")
        if self.event_at.tzinfo is None or self.event_at.utcoffset() is None:
            raise ValueError("event_at must be timezone-aware")
        if isinstance(self.event_date, datetime) or not isinstance(self.event_date, date):
            raise ValueError("event_date must be a date")
        if not isinstance(self.event_time_status, TrackedEventTimeStatus):
            raise ValueError("event_time_status must be a TrackedEventTimeStatus")
        if not isinstance(self.source, MarketEventSource):
            raise ValueError("source must be a MarketEventSource")
        if not isinstance(self.kind, MarketEventKind):
            raise ValueError("kind must be a MarketEventKind")

        object.__setattr__(self, "tracked_event_id", tracked_event_id)
        object.__setattr__(self, "tracked_instrument_id", tracked_instrument_id)
        object.__setattr__(self, "instrument", instrument)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "title", title)
