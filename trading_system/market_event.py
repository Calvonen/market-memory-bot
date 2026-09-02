from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from trading_system.tracked_instruments import _normalise_symbol


class MarketEventSource(str, Enum):
    CALENDAR = "calendar"
    RELEASE = "release"
    MANUAL = "manual"
    NEWS = "news"
    SCANNER = "scanner"


class MarketEventKind(str, Enum):
    EARNINGS = "earnings"
    MARKET_OPEN = "market_open"
    GUIDANCE = "guidance"
    TRADING_UPDATE = "trading_update"
    ACQUISITION = "acquisition"
    DIVIDEND = "dividend"
    MANAGEMENT_CHANGE = "management_change"
    NEWS = "news"
    CUSTOM = "custom"


@dataclass(frozen=True)
class MarketEvent:
    """Generic event identity shared by calendar, release, manual, news and scanner producers.

    This contract is intentionally independent of event expectations, broker
    resolution, market data, strategy, risk and trading. Producers only describe
    what happened (or is scheduled to happen) for one tracked instrument; later
    layers decide how to analyze it.
    """

    event_id: str
    tracked_instrument_id: str
    instrument: str
    market: str
    event_at: datetime
    source: MarketEventSource
    kind: MarketEventKind
    title: str = ""

    def __post_init__(self) -> None:
        event_id = self.event_id.strip()
        tracked_instrument_id = self.tracked_instrument_id.strip()
        instrument = _normalise_symbol(self.instrument)
        market = " ".join(self.market.strip().split()).upper()
        title = self.title.strip()

        if not event_id:
            raise ValueError("event_id must not be blank")
        if not tracked_instrument_id:
            raise ValueError("tracked_instrument_id must not be blank")
        if not instrument:
            raise ValueError("instrument must not be blank")
        if not market:
            raise ValueError("market must not be blank")
        if self.event_at.tzinfo is None or self.event_at.utcoffset() is None:
            raise ValueError("event_at must be timezone-aware")
        if not isinstance(self.source, MarketEventSource):
            raise ValueError("source must be a MarketEventSource")
        if not isinstance(self.kind, MarketEventKind):
            raise ValueError("kind must be a MarketEventKind")

        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "tracked_instrument_id", tracked_instrument_id)
        object.__setattr__(self, "instrument", instrument)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "title", title)
