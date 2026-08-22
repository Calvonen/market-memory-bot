from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any, cast

from trading_system.tracked_etoro_live import (
    EtoroInstrumentStream,
    TrackedEtoroMarketUpdate,
    stream_tracked_etoro_instrument,
)
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument

DEFAULT_QUEUE_MAXSIZE = 32


async def stream_tracked_etoro_instruments(
    tracked_instruments: Iterable[TrackedEtoroInstrument],
    provider: EtoroInstrumentStream,
    *,
    reconnect: bool = True,
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
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

    The merge queue is bounded by ``queue_maxsize`` so a slow consumer
    applies backpressure to producers instead of letting unread updates
    accumulate in memory without limit. Each worker's own final "done" (and
    any "error") signal is still delivered even against a full queue: once
    the merge loop stops draining the queue it cancels every worker, and
    ``asyncio.Queue.put`` is cancellation-safe, so a worker blocked on a
    full queue is unblocked by that cancellation rather than left waiting
    forever.
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

    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)

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
