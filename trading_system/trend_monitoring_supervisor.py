from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime
from trading_system.trend_monitoring_targets import TrendMonitoringTargets


TrendTargetSelector = Callable[[], TrendMonitoringTargets]


def _target_identity(item) -> tuple[object, ...]:
    return (
        item.instrument,
        item.market,
        item.etoro_instrument_id,
        item.etoro_symbol,
        item.etoro_display_name,
        item.etoro_market,
    )


def _resolved_identity_by_id(targets: TrendMonitoringTargets) -> dict[str, tuple[object, ...]]:
    identities: dict[str, tuple[object, ...]] = {}
    for item in targets.resolved:
        tracked_id = item.tracked_instrument_id.strip()
        if not tracked_id:
            raise RuntimeError("resolved Trend target has blank tracked instrument id")
        if tracked_id in identities:
            raise RuntimeError("duplicate resolved Trend tracked instrument id")
        identities[tracked_id] = _target_identity(item)
    return identities


def _snapshot_identity(targets: TrendMonitoringTargets) -> tuple[tuple[object, ...], ...]:
    resolved = tuple(
        sorted((tracked_id, *identity) for tracked_id, identity in _resolved_identity_by_id(targets).items())
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
    unchanged targets is preserved. State is discarded when a target leaves the
    resolved canonical set or when its resolved instrument/broker identity changes,
    so a replacement stream can never inherit incompatible EMA/confirmation state.
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
        self._resolved_identities: dict[str, tuple[object, ...]] = {}

    @property
    def targets(self) -> TrendMonitoringTargets | None:
        return self._targets

    def refresh(self) -> TrendTargetRefresh:
        targets = self._select_targets()
        if not isinstance(targets, TrendMonitoringTargets):
            raise TypeError("selector must return canonical TrendMonitoringTargets")

        resolved_identities = _resolved_identity_by_id(targets)
        identity = _snapshot_identity(targets)
        resolved_ids = set(resolved_identities)

        discarded = set(self._runtime.retain_tracked_instruments(resolved_ids))
        for tracked_id, target_identity in resolved_identities.items():
            runtime_identity = self._runtime.tracked_instrument_identity(tracked_id)
            if runtime_identity is None:
                continue
            target_runtime_identity = target_identity[:3]
            if (
                runtime_identity.instrument,
                runtime_identity.market,
                runtime_identity.etoro_instrument_id,
            ) != target_runtime_identity:
                if self._runtime.discard_tracked_instrument(tracked_id):
                    discarded.add(tracked_id)

        restart_required = self._identity is None or identity != self._identity
        self._targets = targets
        self._identity = identity
        self._resolved_identities = resolved_identities
        return TrendTargetRefresh(
            targets=targets,
            restart_required=restart_required,
            discarded_runtime_state_ids=tuple(sorted(discarded)),
        )
