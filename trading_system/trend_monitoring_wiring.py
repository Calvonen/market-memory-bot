from __future__ import annotations

import os
from collections.abc import Callable
from math import isfinite

from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.tracked_candle_pipeline import TrackedCandlePipeline
from trading_system.tracked_instrument_registry import SupabaseTrackedInstrumentRegistry
from trading_system.tracking_profile_registry import SupabaseTrackedInstrumentProfileRegistry
from trading_system.trend_monitoring_live import stream_trend_monitoring_runtime
from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime
from trading_system.trend_monitoring_service import stream_supervised_trend_monitoring
from trading_system.trend_monitoring_supervisor import TrendMonitoringSupervisor
from trading_system.trend_monitoring_targets import (
    TrendMonitoringTargets,
    select_trend_monitoring_targets,
)


DEFAULT_TREND_REFRESH_SECONDS = 60.0
TargetSnapshotObserver = Callable[[TrendMonitoringTargets], None]


def trend_refresh_interval_seconds() -> float:
    """Read the production Trend target-refresh interval from backend-only env."""
    raw = os.environ.get(
        "MARKETAI_TREND_REFRESH_SECONDS",
        str(DEFAULT_TREND_REFRESH_SECONDS),
    ).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("MARKETAI_TREND_REFRESH_SECONDS must be a positive number") from exc
    if not isfinite(value) or value <= 0:
        raise RuntimeError("MARKETAI_TREND_REFRESH_SECONDS must be a positive number")
    return value


def build_trend_monitoring_service_from_env(
    *,
    on_target_snapshot: TargetSnapshotObserver | None = None,
):
    """Wire the reviewed observation-only Trend service to production backends.

    This factory intentionally stops at dependency wiring. It reads canonical
    tracked instruments and tracking profiles from Supabase, resolves them
    conservatively through eToro, and feeds the existing supervised live Trend
    service. It does not persist Trend observations, create tracked events, or
    invoke Strategy/Risk/Broker/PAPER/LIVE paths.

    ``on_target_snapshot`` is an optional observation-only diagnostic hook. It is
    called with each canonical target snapshot selected by the supervisor and
    must not mutate target selection or trading state.

    Process lifecycle/systemd installation is kept outside this factory so the
    dependency boundary can be reviewed independently before anything is enabled
    on a host.
    """
    provider = EtoroMarketDataProvider.from_env()
    resolver = EtoroInstrumentResolver(provider)
    tracked_reader = SupabaseTrackedInstrumentRegistry.from_env()
    profile_reader = SupabaseTrackedInstrumentProfileRegistry.from_env()
    runtime = TrendMonitoringRuntime()
    candle_pipeline = TrackedCandlePipeline()

    def select_targets() -> TrendMonitoringTargets:
        targets = select_trend_monitoring_targets(
            tracked_reader,
            profile_reader,
            resolver,
        )
        if on_target_snapshot is not None:
            on_target_snapshot(targets)
        return targets

    supervisor = TrendMonitoringSupervisor(
        select_targets=select_targets,
        runtime=runtime,
    )

    def stream_factory(targets):
        return stream_trend_monitoring_runtime(
            targets,
            provider,
            candle_pipeline,
            runtime,
        )

    return stream_supervised_trend_monitoring(
        supervisor=supervisor,
        candle_pipeline=candle_pipeline,
        stream_factory=stream_factory,
        refresh_interval_seconds=trend_refresh_interval_seconds(),
    )
