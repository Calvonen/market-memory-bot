from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from trading_system.reaction_monitoring_profile import (
    SUPPORTED_REACTION_INTERVALS,
    ReactionMonitoringProfile,
)


TRACKING_CONFIG_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TrackedEventMonitoringStageSnapshot:
    start_after_minutes: int
    interval_minutes: int

    def __post_init__(self) -> None:
        # bool is a subclass of int (True == 1, False == 0), so it would
        # otherwise silently pass every numeric check below (e.g. True
        # would look like a valid start_after_minutes=1 / interval_minutes=1)
        # while producing a JSON `true`/`false` the DB contract rejects.
        if isinstance(self.start_after_minutes, bool):
            raise ValueError("start_after_minutes must not be a boolean")
        if self.start_after_minutes < 0:
            raise ValueError("start_after_minutes must be non-negative")
        if not float(self.start_after_minutes).is_integer():
            raise ValueError("start_after_minutes must be a whole minute")
        if isinstance(self.interval_minutes, bool):
            raise ValueError("interval_minutes must not be a boolean")
        # Kept in exact sync with the SQL contract's interval_minutes check
        # (is_valid_tracked_event_config_snapshot_v1 in the 20260830090000
        # migration) - a snapshot the DB would reject must never be
        # constructible here in the first place.
        if self.interval_minutes not in SUPPORTED_REACTION_INTERVALS:
            raise ValueError("interval_minutes must be one of 1, 5 or 15")

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
        # math.isfinite() explicitly rejects NaN and +/-Infinity. A bare
        # "<= 0" comparison alone would let NaN and +Infinity slip through as
        # "positive" (NaN compares false to everything, so "nan <= 0" is
        # false; "+inf <= 0" is also false) even though neither is a
        # meaningful runtime setting.
        #
        # bool is a subclass of int, so it must be rejected explicitly too:
        # True/False would otherwise pass as a "finite positive" 1.0/0.0
        # while producing a JSON `true`/`false` the DB contract rejects.
        if isinstance(self.monitor_hours, bool):
            raise ValueError("monitor_hours must not be a boolean")
        if not math.isfinite(self.monitor_hours) or self.monitor_hours <= 0:
            raise ValueError("monitor_hours must be a finite positive number")
        if isinstance(self.reference_lead_seconds, bool):
            raise ValueError("reference_lead_seconds must not be a boolean")
        if not math.isfinite(self.reference_lead_seconds) or self.reference_lead_seconds <= 0:
            raise ValueError("reference_lead_seconds must be a finite positive number")
        if isinstance(self.max_wait_for_market_hours, bool):
            raise ValueError("max_wait_for_market_hours must not be a boolean")
        if not math.isfinite(self.max_wait_for_market_hours) or self.max_wait_for_market_hours <= 0:
            raise ValueError("max_wait_for_market_hours must be a finite positive number")
        if not self.reaction_stages:
            raise ValueError("reaction_stages must not be empty")
        # Same ordering contract as the SQL validator: first stage at event
        # time, strictly increasing thereafter.
        if self.reaction_stages[0].start_after_minutes != 0:
            raise ValueError("first reaction stage must start at 0 minutes")
        previous_start = -1
        for stage in self.reaction_stages:
            if stage.start_after_minutes <= previous_start:
                raise ValueError("reaction stage start_after_minutes must be strictly increasing")
            previous_start = stage.start_after_minutes
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
