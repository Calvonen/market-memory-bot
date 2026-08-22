from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


class TrackedInstrumentSource(str, Enum):
    SCANNER = "scanner"
    CALENDAR = "calendar"
    MANUAL = "manual"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalise_symbol(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().upper())


def _normalise_market(value: str) -> str:
    return " ".join(value.strip().upper().split())


@dataclass(frozen=True)
class TrackedInstrument:
    """A user-selected instrument that may later receive many different events.

    Tracking an instrument is intentionally separate from creating an event,
    expectation, strategy decision, or trade candidate. Multiple discovery
    sources can point to the same tracked instrument.
    """

    instrument: str
    company_name: str = ""
    market: str = ""
    sources: tuple[TrackedInstrumentSource, ...] = ()
    active: bool = True
    tracked_instrument_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        """Enforce identity invariants for every construction path (factory, direct, replace)."""
        symbol = _normalise_symbol(self.instrument)
        if not symbol:
            raise ValueError("instrument is required")
        object.__setattr__(self, "instrument", symbol)
        object.__setattr__(self, "market", self.market.strip())

    @property
    def key(self) -> tuple[str, str]:
        """Stable identity key without guessing across different markets."""
        return (_normalise_symbol(self.instrument), _normalise_market(self.market))


def create_tracked_instrument(
    *,
    instrument: str,
    company_name: str = "",
    market: str = "",
    source: TrackedInstrumentSource,
    now: datetime | None = None,
) -> TrackedInstrument:
    timestamp = now or _utc_now()
    return TrackedInstrument(
        instrument=instrument,
        company_name=company_name.strip(),
        market=market,
        sources=(source,),
        created_at=timestamp,
        updated_at=timestamp,
    )


def add_tracking_source(
    tracked: TrackedInstrument,
    source: TrackedInstrumentSource,
    *,
    now: datetime | None = None,
) -> TrackedInstrument:
    """Return an active copy with a discovery source merged idempotently."""
    sources = tracked.sources
    if source not in sources:
        sources = (*sources, source)

    if sources == tracked.sources and tracked.active:
        return tracked

    return replace(
        tracked,
        sources=sources,
        active=True,
        updated_at=now or _utc_now(),
    )


def set_tracking_active(
    tracked: TrackedInstrument,
    active: bool,
    *,
    now: datetime | None = None,
) -> TrackedInstrument:
    """Activate/deactivate tracking without deleting its identity or sources."""
    if tracked.active is active:
        return tracked
    return replace(tracked, active=active, updated_at=now or _utc_now())
