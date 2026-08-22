from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any, cast

from trading_system.tracked_etoro_live import (
    EtoroLiveStreamProvider,
    TrackedEtoroMarketUpdate,
    stream_tracked_etoro_instrument,
)
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


async def stream_tracked_etoro_instruments(
    tracked_instruments: Iterable[TrackedEtoroInstrument],
    provider: EtoroLiveStreamProvider,
    *,
    reconnect: bool = True,
) -> AsyncIterator[TrackedEtoroMarketUpdate]:
    """Merge live updates for multiple resolved tracked instruments.

    One worker is started for each resolved eToro instrument because the
    current provider contract streams one instrument per subscription. The
    orchestrator only merges those already-validated streams; it does not
    resolve instruments, persist state, build candles, or make trading
    decisions.

    Duplicate tracked identities or duplicate resolved eToro IDs fail closed
    before any stream is started. If any worker fails, the exception is
    propagated and all sibling workers are cancelled.
    """
    instruments = tuple(tracked_instruments)
    if not instruments:
        return

    tracked_ids = [item.tracked_instrument_id for item in instruments]
    if len(set(tracked_ids)) != len(tracked_ids):
        raise ValueError("duplicate tracked_instrument_id")

    etoro_ids = [item.etoro_instrument_id for item in instruments]
    if len(set(etoro_ids)) != len(etoro_ids):
        raise ValueError("duplicate etoro_instrument_id")

    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def worker(item: TrackedEtoroInstrument) -> None:
        try:
            async for update in stream_tracked_etoro_instrument(
                item,
                provider,
                reconnect=reconnect,
            ):
                await queue.put(("update", update))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(("error", exc))
        finally:
            await queue.put(("done", item.tracked_instrument_id))

    tasks = [asyncio.create_task(worker(item)) for item in instruments]
    remaining = len(tasks)
    try:
        while remaining:
            kind, payload = await queue.get()
            if kind == "update":
                yield cast(TrackedEtoroMarketUpdate, payload)
            elif kind == "error":
                raise cast(Exception, payload)
            elif kind == "done":
                remaining -= 1
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
