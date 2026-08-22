from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


class EtoroInstrumentStream(Protocol):
    def stream_instrument(
        self,
        instrument_id: int,
        *,
        reconnect: bool = True,
    ) -> AsyncIterator[EtoroMarketUpdate]: ...


@dataclass(frozen=True)
class TrackedEtoroMarketUpdate:
    """One eToro market update tied back to the tracked-instrument identity."""

    tracked_instrument_id: str
    instrument: str
    market: str
    etoro_instrument_id: int
    update: EtoroMarketUpdate


async def stream_tracked_etoro_instrument(
    tracked: TrackedEtoroInstrument,
    provider: EtoroInstrumentStream,
    *,
    reconnect: bool = True,
) -> AsyncIterator[TrackedEtoroMarketUpdate]:
    """Stream one resolved tracked instrument without adding trading side effects.

    The resolved eToro ID is the subscription authority. Updates for any other
    instrument are ignored defensively even though the underlying provider is
    already expected to filter its single-instrument stream.
    """
    async for update in provider.stream_instrument(
        tracked.etoro_instrument_id,
        reconnect=reconnect,
    ):
        if update.instrument_id != tracked.etoro_instrument_id:
            continue
        yield TrackedEtoroMarketUpdate(
            tracked_instrument_id=tracked.tracked_instrument_id,
            instrument=tracked.instrument,
            market=tracked.market,
            etoro_instrument_id=tracked.etoro_instrument_id,
            update=update,
        )
