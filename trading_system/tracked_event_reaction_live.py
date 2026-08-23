from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass

from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_etoro_live import EtoroInstrumentStream, TrackedEtoroMarketUpdate
from trading_system.tracked_etoro_orchestrator import (
    DEFAULT_QUEUE_MAXSIZE,
    stream_tracked_etoro_instruments,
)
from trading_system.tracked_event_reaction_runtime import TrackedEventReactionRuntime
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


@dataclass(frozen=True)
class TrackedRuntimeMarketBatch:
    """One live tracked market update and the closed candles it produced."""

    update: TrackedEtoroMarketUpdate
    candles: tuple[TrackedMarketCandle, ...]


async def stream_tracked_event_reaction_runtime(
    tracked_instruments: Iterable[TrackedEtoroInstrument],
    provider: EtoroInstrumentStream,
    runtime: TrackedEventReactionRuntime,
    *,
    reconnect: bool = True,
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
) -> AsyncIterator[TrackedRuntimeMarketBatch]:
    """Feed the existing multi-instrument live stream into one reaction runtime.

    The caller owns ``runtime`` so event observations can use the same continuously
    accumulated candle/history/reaction state while this stream is running. This
    adapter does not discover events, observe events automatically, persist data,
    or make trading decisions.

    Every accepted tracked market update is passed exactly once to
    ``runtime.add_market_update``. The returned item preserves that update together
    with the exact closed-candle batch produced by the runtime, including empty
    batches when no candle closed on that market update.
    """
    async for update in stream_tracked_etoro_instruments(
        tracked_instruments,
        provider,
        reconnect=reconnect,
        queue_maxsize=queue_maxsize,
    ):
        candles = runtime.add_market_update(update)
        yield TrackedRuntimeMarketBatch(update=update, candles=candles)
