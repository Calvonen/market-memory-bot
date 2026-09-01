from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass

from trading_system.tracked_candle_pipeline import TrackedCandlePipeline, TrackedMarketCandle
from trading_system.tracked_etoro_live import EtoroInstrumentStream, TrackedEtoroMarketUpdate
from trading_system.tracked_etoro_orchestrator import (
    DEFAULT_QUEUE_MAXSIZE,
    stream_tracked_etoro_instruments,
)
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime, TrendRuntimeResult


@dataclass(frozen=True)
class TrendRuntimeMarketBatch:
    """One selected live update, its closed candles, and Trend observations."""

    update: TrackedEtoroMarketUpdate
    candles: tuple[TrackedMarketCandle, ...]
    trend_results: tuple[TrendRuntimeResult, ...]


async def stream_trend_monitoring_runtime(
    tracked_instruments: Iterable[TrackedEtoroInstrument],
    provider: EtoroInstrumentStream,
    candle_pipeline: TrackedCandlePipeline,
    runtime: TrendMonitoringRuntime,
    *,
    reconnect: bool = True,
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
) -> AsyncIterator[TrendRuntimeMarketBatch]:
    """Feed one prevalidated Trend target snapshot into the existing live path.

    ``tracked_instruments`` must be the resolved, active, enabled-Trend target
    snapshot produced by the canonical target-selection boundary. This adapter
    deliberately does not re-read persistence or refresh that snapshot; the
    service lifecycle that owns target refresh/restart is a separate concern.

    Every upstream tracked market update is fed exactly once to the existing
    ``TrackedCandlePipeline``. Every newly closed candle is then offered exactly
    once to the supplied ``TrendMonitoringRuntime``. The runtime itself ignores
    non-15-minute candles, so this adapter does not create a parallel candle path
    or re-aggregate market data.

    The three prerequisite flags are true because entry into this adapter is
    restricted to that prevalidated target snapshot: canonical instrument active,
    Trend profile enabled, and eToro identity resolved. A caller must rebuild the
    stream when that snapshot changes rather than mutating these facts in place.

    No observations are persisted and no event, Strategy, Risk, Broker, PAPER, or
    LIVE trading path is invoked here.
    """
    instruments = tuple(tracked_instruments)
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
