from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from trading_system.tracked_candle_pipeline import TrackedCandlePipeline, TrackedMarketCandle
from trading_system.tracked_etoro_live import EtoroInstrumentStream, TrackedEtoroMarketUpdate
from trading_system.tracked_etoro_orchestrator import (
    DEFAULT_QUEUE_MAXSIZE,
    stream_tracked_etoro_instruments,
)
from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime, TrendRuntimeResult
from trading_system.trend_monitoring_targets import TrendMonitoringTargets


@dataclass(frozen=True)
class TrendRuntimeMarketBatch:
    """One selected live update, its closed candles, and Trend observations."""

    update: TrackedEtoroMarketUpdate
    candles: tuple[TrackedMarketCandle, ...]
    trend_results: tuple[TrendRuntimeResult, ...]


async def stream_trend_monitoring_runtime(
    targets: TrendMonitoringTargets,
    provider: EtoroInstrumentStream,
    candle_pipeline: TrackedCandlePipeline,
    runtime: TrendMonitoringRuntime,
    *,
    reconnect: bool = True,
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
) -> AsyncIterator[TrendRuntimeMarketBatch]:
    """Feed one canonical, provenance-bearing Trend target snapshot into the live path.

    ``targets`` must be the snapshot returned by
    ``select_trend_monitoring_targets(...)``. The target type itself carries the
    canonical-selection boundary, so this adapter never accepts an arbitrary
    iterable of broker-resolved instruments and then invents prerequisite truth.

    Every upstream tracked market update is fed exactly once to the existing
    ``TrackedCandlePipeline``. Every newly closed candle is then offered exactly
    once to the supplied ``TrendMonitoringRuntime``. The runtime itself ignores
    non-15-minute candles, so this adapter does not create a parallel candle path
    or re-aggregate market data.

    The three prerequisite flags are true because entry into this adapter is
    restricted to the canonical snapshot: canonical instrument active, Trend
    profile enabled, and eToro identity resolved. A caller must rebuild the stream
    when that snapshot changes rather than mutating these facts in place.

    No observations are persisted and no event, Strategy, Risk, Broker, PAPER, or
    LIVE trading path is invoked here.
    """
    if not isinstance(targets, TrendMonitoringTargets):
        raise TypeError("targets must be canonical TrendMonitoringTargets")

    instruments = targets.resolved
    if not instruments:
        return

    upstream = stream_tracked_etoro_instruments(
        instruments,
        provider,
        reconnect=reconnect,
        queue_maxsize=queue_maxsize,
    )
    try:
        async for update in upstream:
            candles = candle_pipeline.add(update)
            trend_results: list[TrendRuntimeResult] = []
            for candle in candles:
                result = runtime.add_candle(
                    candle,
                    instrument_active=True,
                    trend_profile_enabled=True,
                    etoro_identity_resolved=True,
                )
                if result is not None:
                    trend_results.append(result)
            yield TrendRuntimeMarketBatch(
                update=update,
                candles=candles,
                trend_results=tuple(trend_results),
            )
    finally:
        await upstream.aclose()
