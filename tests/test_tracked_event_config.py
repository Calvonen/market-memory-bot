from __future__ import annotations

import math
import unittest
from datetime import timedelta

from trading_system.reaction_monitoring_profile import (
    DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    ReactionMonitoringProfile,
    ReactionMonitoringStage,
)
from trading_system.tracked_event_config import (
    TRACKING_CONFIG_SCHEMA_VERSION,
    TrackedEventConfigSnapshot,
    TrackedEventMonitoringStageSnapshot,
    snapshot_effective_tracking_config,
)


class TrackedEventConfigSnapshotTests(unittest.TestCase):
    def test_default_effective_config_serializes_stable_snapshot(self) -> None:
        snapshot = snapshot_effective_tracking_config(
            monitor_hours=8.0,
            reference_lead_seconds=30.0,
            max_wait_for_market_hours=72.0,
            profile=DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
        )

        self.assertEqual(
            snapshot.to_dict(),
            {
                "schema_version": TRACKING_CONFIG_SCHEMA_VERSION,
                "monitor_hours": 8.0,
                "reference_lead_seconds": 30.0,
                "max_wait_for_market_hours": 72.0,
                "reaction_stages": [
                    {"start_after_minutes": 0, "interval_minutes": 1},
                    {"start_after_minutes": 30, "interval_minutes": 5},
                    {"start_after_minutes": 150, "interval_minutes": 15},
                ],
            },
        )

    def test_snapshot_rejects_non_positive_runtime_values(self) -> None:
        for field in (
            "monitor_hours",
            "reference_lead_seconds",
            "max_wait_for_market_hours",
        ):
            kwargs = {
                "monitor_hours": 8.0,
                "reference_lead_seconds": 30.0,
                "max_wait_for_market_hours": 72.0,
                "reaction_stages": snapshot_effective_tracking_config(
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                    profile=DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
                ).reaction_stages,
            }
            kwargs[field] = 0.0
            with self.subTest(field=field), self.assertRaises(ValueError):
                TrackedEventConfigSnapshot(**kwargs)

    def test_snapshot_requires_whole_minute_stage_boundaries(self) -> None:
        profile = ReactionMonitoringProfile(
            stages=(
                ReactionMonitoringStage(start_after=timedelta(0), interval_minutes=1),
                ReactionMonitoringStage(start_after=timedelta(seconds=90), interval_minutes=5),
            )
        )
        with self.assertRaisesRegex(ValueError, "whole minutes"):
            snapshot_effective_tracking_config(
                monitor_hours=8.0,
                reference_lead_seconds=30.0,
                max_wait_for_market_hours=72.0,
                profile=profile,
            )

    def test_stage_rejects_unsupported_interval(self) -> None:
        with self.assertRaisesRegex(ValueError, "1, 5 or 15"):
            TrackedEventMonitoringStageSnapshot(start_after_minutes=0, interval_minutes=7)

    def test_snapshot_rejects_first_stage_not_at_zero(self) -> None:
        with self.assertRaisesRegex(ValueError, "first reaction stage"):
            TrackedEventConfigSnapshot(
                monitor_hours=8.0,
                reference_lead_seconds=30.0,
                max_wait_for_market_hours=72.0,
                reaction_stages=(
                    TrackedEventMonitoringStageSnapshot(start_after_minutes=5, interval_minutes=1),
                ),
            )

    def test_snapshot_rejects_non_increasing_stage_starts(self) -> None:
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            TrackedEventConfigSnapshot(
                monitor_hours=8.0,
                reference_lead_seconds=30.0,
                max_wait_for_market_hours=72.0,
                reaction_stages=(
                    TrackedEventMonitoringStageSnapshot(start_after_minutes=0, interval_minutes=1),
                    TrackedEventMonitoringStageSnapshot(start_after_minutes=30, interval_minutes=5),
                    TrackedEventMonitoringStageSnapshot(start_after_minutes=30, interval_minutes=15),
                ),
            )

    def test_snapshot_rejects_empty_reaction_stages(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            TrackedEventConfigSnapshot(
                monitor_hours=8.0,
                reference_lead_seconds=30.0,
                max_wait_for_market_hours=72.0,
                reaction_stages=(),
            )

    def test_snapshot_rejects_unsupported_schema_version(self) -> None:
        valid_stages = snapshot_effective_tracking_config(
            monitor_hours=8.0,
            reference_lead_seconds=30.0,
            max_wait_for_market_hours=72.0,
            profile=DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
        ).reaction_stages
        with self.assertRaisesRegex(ValueError, "unsupported"):
            TrackedEventConfigSnapshot(
                monitor_hours=8.0,
                reference_lead_seconds=30.0,
                max_wait_for_market_hours=72.0,
                reaction_stages=valid_stages,
                schema_version=2,
            )

    def test_stage_rejects_fractional_start_after_minutes(self) -> None:
        with self.assertRaisesRegex(ValueError, "whole minute"):
            TrackedEventMonitoringStageSnapshot(start_after_minutes=0.5, interval_minutes=1)

    def test_snapshot_rejects_non_finite_runtime_values(self) -> None:
        valid_stages = snapshot_effective_tracking_config(
            monitor_hours=8.0,
            reference_lead_seconds=30.0,
            max_wait_for_market_hours=72.0,
            profile=DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
        ).reaction_stages
        for field in (
            "monitor_hours",
            "reference_lead_seconds",
            "max_wait_for_market_hours",
        ):
            for bad_value in (math.nan, math.inf, -math.inf):
                kwargs = {
                    "monitor_hours": 8.0,
                    "reference_lead_seconds": 30.0,
                    "max_wait_for_market_hours": 72.0,
                    "reaction_stages": valid_stages,
                }
                kwargs[field] = bad_value
                with (
                    self.subTest(field=field, bad_value=bad_value),
                    self.assertRaisesRegex(ValueError, "finite positive"),
                ):
                    TrackedEventConfigSnapshot(**kwargs)


if __name__ == "__main__":
    unittest.main()
