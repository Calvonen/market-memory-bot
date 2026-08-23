from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from trading_system.reaction_monitoring_profile import ReactionMonitoringProfile


TRACKING_CONFIG_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TrackedEventMonitoringStageSnapshot:
    start_after_minutes: int
    interval_minutes: int

    def __post_init__(self) -> None:
        if self.start_after_minutes < 0:
            raise ValueError("start_after_minutes must be non-negative")
        if self.interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")

    def to_dict(self) -> dict[str, int]:
        return {
            "start_after_minutes": self.start_after_minutes,
            "interval_minutes": self.interval_minutes,
        }


@dataclass(frozen=True)
class TrackedEventConfigSnapshot:
    """Immutable description of the effective settings used for one tracked event.

    This is deliberately observation-only metadata. It records the settings that
    governed reaction monitoring so a later UI/history summary does not accidentally
    render today's global defaults as if they had applied to an older event.
    """

    monitor_hours: float
    reference_lead_seconds: float
    max_wait_for_market_hours: float
    reaction_stages: tuple[TrackedEventMonitoringStageSnapshot, ...]
    schema_version: int = TRACKING_CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.monitor_hours <= 0:
            raise ValueError("monitor_hours must be positive")
        if self.reference_lead_seconds <= 0:
            raise ValueError("reference_lead_seconds must be positive")
        if self.max_wait_for_market_hours <= 0:
            raise ValueError("max_wait_for_market_hours must be positive")
        if not self.reaction_stages:
            raise ValueError("reaction_stages must not be empty")
        if self.schema_version != TRACKING_CONFIG_SCHEMA_VERSION:
            raise ValueError("unsupported tracking config schema_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "monitor_hours": self.monitor_hours,
            "reference_lead_seconds": self.reference_lead_seconds,
            "max_wait_for_market_hours": self.max_wait_for_market_hours,
            "reaction_stages": [stage.to_dict() for stage in self.reaction_stages],
        }


def snapshot_effective_tracking_config(
    *,
    monitor_hours: float,
    reference_lead_seconds: float,
    max_wait_for_market_hours: float,
    profile: ReactionMonitoringProfile,
) -> TrackedEventConfigSnapshot:
    stages: list[TrackedEventMonitoringStageSnapshot] = []
    for stage in profile.stages:
        total_minutes = stage.start_after / timedelta(minutes=1)
        if not total_minutes.is_integer():
            raise ValueError("reaction stage start_after must be whole minutes for persistence")
        stages.append(
            TrackedEventMonitoringStageSnapshot(
                start_after_minutes=int(total_minutes),
                interval_minutes=stage.interval_minutes,
            )
        )
    return TrackedEventConfigSnapshot(
        monitor_hours=monitor_hours,
        reference_lead_seconds=reference_lead_seconds,
        max_wait_for_market_hours=max_wait_for_market_hours,
        reaction_stages=tuple(stages),
    )
