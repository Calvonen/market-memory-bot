from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from trading_system.reaction_monitoring_profile import (
    DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    ReactionMonitoringProfile,
    ReactionMonitoringStage,
)


class ReactionMonitoringProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event_at = datetime(2026, 8, 23, 14, 32, 30, tzinfo=UTC)

    def test_default_profile_switches_1m_to_5m_to_15m_from_arbitrary_event_time(self) -> None:
        profile = DEFAULT_EVENT_REACTION_MONITORING_PROFILE

        self.assertIsNone(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at - timedelta(seconds=1),
            )
        )
        self.assertEqual(profile.interval_for(event_at=self.event_at, observed_at=self.event_at), 1)
        self.assertEqual(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at + timedelta(minutes=29, seconds=59),
            ),
            1,
        )
        self.assertEqual(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at + timedelta(minutes=30),
            ),
            5,
        )
        self.assertEqual(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at + timedelta(minutes=149, seconds=59),
            ),
            5,
        )
        self.assertEqual(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at + timedelta(minutes=150),
            ),
            15,
        )

    def test_custom_profile_can_keep_one_minute_analysis_longer(self) -> None:
        profile = ReactionMonitoringProfile(
            stages=(
                ReactionMonitoringStage(timedelta(0), 1),
                ReactionMonitoringStage(timedelta(minutes=60), 5),
                ReactionMonitoringStage(timedelta(hours=4), 15),
            )
        )

        self.assertEqual(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at + timedelta(minutes=45),
            ),
            1,
        )
        self.assertEqual(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at + timedelta(minutes=60),
            ),
            5,
        )

    def test_profile_is_not_tied_to_market_open_time(self) -> None:
        midday_event = datetime(2026, 8, 23, 12, 17, 43, tzinfo=UTC)

        self.assertEqual(
            DEFAULT_EVENT_REACTION_MONITORING_PROFILE.interval_for(
                event_at=midday_event,
                observed_at=midday_event + timedelta(minutes=10),
            ),
            1,
        )

    def test_invalid_stage_configuration_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "1, 5 or 15"):
            ReactionMonitoringStage(timedelta(0), 2)

        with self.assertRaisesRegex(ValueError, "first stage"):
            ReactionMonitoringProfile(
                stages=(ReactionMonitoringStage(timedelta(minutes=1), 1),)
            )

        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            ReactionMonitoringProfile(
                stages=(
                    ReactionMonitoringStage(timedelta(0), 1),
                    ReactionMonitoringStage(timedelta(0), 5),
                )
            )

    def test_timestamps_must_be_timezone_aware(self) -> None:
        profile = DEFAULT_EVENT_REACTION_MONITORING_PROFILE
        naive = datetime(2026, 8, 23, 14, 32, 30)

        with self.assertRaisesRegex(ValueError, "event_at"):
            profile.interval_for(event_at=naive, observed_at=self.event_at)
        with self.assertRaisesRegex(ValueError, "observed_at"):
            profile.interval_for(event_at=self.event_at, observed_at=naive)


if __name__ == "__main__":
    unittest.main()
