from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

import requests

from trading_system.tracked_candle_pipeline import TrackedCandlePipeline
from trading_system.trend_monitoring_live import TrendRuntimeMarketBatch
from trading_system.trend_monitoring_supervisor import TrendMonitoringSupervisor
from trading_system.trend_monitoring_targets import TrendMonitoringTargets


logger = logging.getLogger(__name__)


class ClosableTrendStream(Protocol):
    def __aiter__(self) -> ClosableTrendStream: ...
    async def __anext__(self) -> TrendRuntimeMarketBatch: ...
    async def aclose(self) -> None: ...


TrendStreamFactory = Callable[[TrendMonitoringTargets], ClosableTrendStream]
SleepFunction = Callable[[float], Awaitable[None]]


def _is_transient_refresh_error(exc: BaseException) -> bool:
    """Return True only for explicitly identified transport failures.

    Canonical validation and identity failures must propagate fail-closed. The
    eToro target resolver wraps requests transport failures in RuntimeError, so
    inspect the causal chain but accept only timeout/connection exceptions.
    JSON decoding and other RequestException subclasses remain fail-closed.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (requests.Timeout, requests.ConnectionError)):
            return True
        current = current.__cause__ or current.__context__
    return False


def _align_candle_pipeline(
    pipeline: TrackedCandlePipeline,
    targets: TrendMonitoringTargets,
) -> tuple[str, ...]:
    """Prune candle state that cannot belong to the selected canonical snapshot."""
    selected: dict[str, tuple[str, str, int]] = {}
    for item in targets.resolved:
        tracked_id = item.tracked_instrument_id.strip()
        if not tracked_id:
            raise RuntimeError("resolved Trend target has blank tracked instrument id")
        if tracked_id in selected:
            raise RuntimeError("duplicate resolved Trend tracked instrument id")
        selected[tracked_id] = (item.instrument, item.market, item.etoro_instrument_id)

    discarded = set(pipeline.retain_tracked_instruments(set(selected)))
    for tracked_id, identity in selected.items():
        current = pipeline.tracked_identity(tracked_id)
        if current is not None and current != identity:
            if pipeline.discard_tracked_instrument(tracked_id):
                discarded.add(tracked_id)
    return tuple(sorted(discarded))


async def _close_stream(
    stream: ClosableTrendStream | None,
    next_batch_task: asyncio.Task[TrendRuntimeMarketBatch] | None,
    *,
    propagate_completed_failure: bool = False,
) -> None:
    completed_failure: BaseException | None = None

    if next_batch_task is not None:
        if not next_batch_task.done():
            next_batch_task.cancel()
            with suppress(asyncio.CancelledError):
                await next_batch_task
        else:
            try:
                next_batch_task.result()
            except asyncio.CancelledError:
                pass
            except StopAsyncIteration as exc:
                if propagate_completed_failure:
                    completed_failure = RuntimeError("Trend live stream ended unexpectedly")
                    completed_failure.__cause__ = exc
            except Exception as exc:
                if propagate_completed_failure:
                    completed_failure = exc

    if stream is not None:
        if completed_failure is None:
            await stream.aclose()
        else:
            # Cleanup must still be attempted, but a close failure must never mask
            # the already-completed provider/stream failure that caused shutdown.
            with suppress(Exception):
                await stream.aclose()

    if completed_failure is not None:
        raise completed_failure


async def stream_supervised_trend_monitoring(
    *,
    supervisor: TrendMonitoringSupervisor,
    candle_pipeline: TrackedCandlePipeline,
    stream_factory: TrendStreamFactory,
    refresh_interval_seconds: float = 60.0,
    sleep: SleepFunction = asyncio.sleep,
):
    """Continuously refresh canonical Trend targets and restart the live stream safely.

    The supervisor owns canonical target selection and Trend-runtime pruning. This
    service loop owns only timing and stream lifecycle. Before any replacement
    stream starts, the shared candle pipeline is aligned to the same canonical
    snapshot so removed or identity-changed targets cannot carry partial candles
    across a monitoring gap.

    A refresh that leaves the snapshot unchanged does not restart the stream.
    Empty snapshots are valid: the service waits for the next refresh without
    opening a market-data stream. Once an initial snapshot has started monitoring,
    an explicitly identified transient transport failure during target refresh
    keeps the last successful snapshot and live stream in place until the next
    refresh attempt. Canonical validation/identity failures, initial selection,
    and unexpected live-stream termination still fail closed.

    This function does not persist observations, create events, or invoke
    Strategy/Risk/Broker/PAPER/LIVE trading paths.
    """
    if refresh_interval_seconds <= 0:
        raise ValueError("refresh_interval_seconds must be positive")

    stream: ClosableTrendStream | None = None
    next_batch_task: asyncio.Task[TrendRuntimeMarketBatch] | None = None
    refresh_task: asyncio.Task[None] | None = None

    async def start_stream(targets: TrendMonitoringTargets) -> None:
        nonlocal stream, next_batch_task
        if not targets.resolved:
            stream = None
            next_batch_task = None
            return
        stream = stream_factory(targets)
        next_batch_task = asyncio.create_task(anext(stream))

    try:
        first = supervisor.refresh()
        _align_candle_pipeline(candle_pipeline, first.targets)
        await start_stream(first.targets)
        refresh_task = asyncio.create_task(sleep(refresh_interval_seconds))

        while True:
            wait_for = {refresh_task}
            if next_batch_task is not None:
                wait_for.add(next_batch_task)
            done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)

            if refresh_task in done:
                refresh_task.result()
                try:
                    refreshed = supervisor.refresh()
                except Exception as exc:
                    if not _is_transient_refresh_error(exc):
                        raise
                    logger.exception(
                        "Trend target refresh hit transient transport failure; keeping last successful snapshot and live stream"
                    )
                    refresh_task = asyncio.create_task(sleep(refresh_interval_seconds))
                else:
                    _align_candle_pipeline(candle_pipeline, refreshed.targets)

                    if refreshed.restart_required:
                        old_stream = stream
                        old_next_batch_task = next_batch_task
                        # Transfer ownership before cleanup so an error from the old
                        # stream cannot make the generator finalizer close it again
                        # and mask the original provider/stream failure.
                        stream = None
                        next_batch_task = None
                        await _close_stream(
                            old_stream,
                            old_next_batch_task,
                            propagate_completed_failure=True,
                        )
                        await start_stream(refreshed.targets)

                    refresh_task = asyncio.create_task(sleep(refresh_interval_seconds))

                    # When refresh and an old-stream batch complete simultaneously,
                    # a changed snapshot wins only over a successful obsolete batch.
                    # Stream termination/provider failures must still propagate.
                    if refreshed.restart_required:
                        continue

            if next_batch_task is not None and next_batch_task in done:
                try:
                    batch = next_batch_task.result()
                except StopAsyncIteration as exc:
                    raise RuntimeError("Trend live stream ended unexpectedly") from exc
                yield batch
                if stream is None:
                    raise RuntimeError("Trend live stream missing after batch")
                next_batch_task = asyncio.create_task(anext(stream))
    finally:
        if refresh_task is not None and not refresh_task.done():
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
        await _close_stream(stream, next_batch_task)
