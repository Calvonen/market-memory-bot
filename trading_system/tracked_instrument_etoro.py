from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_system.etoro_instrument_resolver import (
    InstrumentResolutionRequest,
    ResolvedEtoroInstrument,
)
from trading_system.tracked_instruments import TrackedInstrument


class InstrumentResolver(Protocol):
    def resolve(self, request: InstrumentResolutionRequest) -> ResolvedEtoroInstrument | None: ...


@dataclass(frozen=True)
class TrackedEtoroInstrument:
    """A resolved eToro identity for one tracked instrument.

    This is only an identity bridge. It does not start a market-data stream,
    persist state, create events, or make trading decisions.
    """

    tracked_instrument_id: str
    instrument: str
    market: str
    etoro_instrument_id: int
    etoro_symbol: str
    etoro_display_name: str


def resolve_tracked_instrument(
    tracked: TrackedInstrument,
    resolver: InstrumentResolver,
) -> TrackedEtoroInstrument | None:
    """Resolve one active tracked instrument to eToro without adding side effects.

    Inactive tracked instruments are intentionally not resolved. Failure or
    ambiguity from the underlying conservative resolver is propagated as
    ``None`` instead of guessing or creating a partial mapping.
    """
    if not tracked.active:
        return None

    resolved = resolver.resolve(
        InstrumentResolutionRequest(
            instrument=tracked.instrument,
            company_name=tracked.company_name,
            market=tracked.market,
        )
    )
    if resolved is None:
        return None

    return TrackedEtoroInstrument(
        tracked_instrument_id=tracked.tracked_instrument_id,
        instrument=tracked.instrument,
        market=tracked.market,
        etoro_instrument_id=resolved.instrument_id,
        etoro_symbol=resolved.symbol,
        etoro_display_name=resolved.display_name,
    )
