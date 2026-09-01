from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime
from trading_system.trend_monitoring_targets import TrendMonitoringTargets


TrendTargetSelector = Callable[[], TrendMonitoringTargets]


def _snapshot_identity(targets: TrendMonitoringTargets) -> tuple[tuple[object, ...], ...]:
    resolved = tuple(
        sorted(
            (
                item.tracked_instrument_id,
                item.instrument,
                item.market,
                item.etoro_instrument_id,
                item.etoro_symbol,
                item.etoro_display_name,
                item.etoro_market,
            )
            for item in targets.resolved
        )
    )
    unresolved = tuple(sorted(targets.unresolved_tracked_instrument_ids))
    return (*resolved, ("__unresolved__", *unresolved))


@dataclass(frozen=True)
class TrendTargetRefresh:
    targets: TrendMonitoringTargets
    restart_required: bool
    discarded_runtime_state_ids: tuple[str, ...]


class TrendMonitoringSupervisor:
    """Own the refresh boundary between canonical Trend selection and live streaming.

    The supervisor deliberately does not open streams or schedule polling itself.
    A service loop can call ``refresh()`` at its chosen cadence and restart the
    existing live adapter only when ``restart_required`` is true. Runtime state for
    targets that remain selected is preserved; state for targets that leave the
    resolved canonical target set is discarded before a replacement stream may
    start.
    """

    def __init__(
        self,
        *,
        select_targets: TrendTargetSelector,
        runtime: TrendMonitoringRuntime,
    ) -> None:
        self._select_targets = select_targets
        self._runtime = runtime
        self._targets: TrendMonitoringTargets | None = None
        self._identity: tuple[tuple[object, ...], ...] | None = None

    @property
    def targets(self) -> TrendMonitoringTargets | None:
        return self._targets

    def refresh(self) -> TrendTargetRefresh:
        targets = self._select_targets()
        if not isinstance(targets, TrendMonitoringTargets):
            raise TypeError("selector must return canonical TrendMonitoringTargets")

        identity = _snapshot_identity(targets)
        resolved_ids = {item.tracked_instrument_id.strip() for item in targets.resolved}
        if "" in resolved_ids:
            raise RuntimeError("resolved Trend target has blank tracked instrument id")
        if len(resolved_ids) != len(targets.resolved):
            raise RuntimeError("duplicate resolved Trend tracked instrument id")

        discarded = self._runtime.retain_tracked_instruments(resolved_ids)
        restart_required = self._identity is None or identity != self._identity
        self._targets = targets
        self._identity = identity
        return TrendTargetRefresh(
            targets=targets,
            restart_required=restart_required,
            discarded_runtime_state_ids=discarded,
        )
