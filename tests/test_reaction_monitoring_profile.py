from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

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
            1,
        )
        self.assertEqual(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at + timedelta(minutes=30, seconds=1),
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
            5,
        )
        self.assertEqual(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at + timedelta(minutes=150, seconds=1),
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
            1,
        )
        self.assertEqual(
            profile.interval_for(
                event_at=self.event_at,
                observed_at=self.event_at + timedelta(minutes=60, seconds=1),
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

    def test_elapsed_time_is_correct_across_autumn_dst_fallback(self) -> None:
        london = ZoneInfo("Europe/London")
        event_at = datetime(2026, 10, 25, 0, 30, tzinfo=london)
        observed_at = datetime(2026, 10, 25, 2, 0, tzinfo=london)

        # 00:30 is still BST (+01:00); by 02:00 the clocks have fallen back to
        # GMT (+00:00), so the real elapsed duration is 2h30m even though the
        # wall-clock digits only advance by 1h30m.
        self.assertEqual(event_at.utcoffset(), timedelta(hours=1))
        self.assertEqual(observed_at.utcoffset(), timedelta(0))
        self.assertEqual(
            observed_at.astimezone(UTC) - event_at.astimezone(UTC),
            timedelta(hours=2, minutes=30),
        )

        self.assertEqual(
            DEFAULT_EVENT_REACTION_MONITORING_PROFILE.interval_for(
                event_at=event_at,
                observed_at=observed_at,
            ),
            5,
        )

    def test_elapsed_time_is_correct_across_spring_dst_forward_and_does_not_switch_stage_early(
        self,
    ) -> None:
        london = ZoneInfo("Europe/London")
        event_at = datetime(2026, 3, 29, 0, 50, tzinfo=london)
        observed_at = datetime(2026, 3, 29, 2, 5, tzinfo=london)

        # 00:50 is GMT (+00:00); clocks spring forward at 01:00 GMT to 02:00
        # BST (+01:00), so 02:05 is only 15 real minutes after 00:50, even
        # though the wall-clock digits advance by 1h15m.
        self.assertEqual(event_at.utcoffset(), timedelta(0))
        self.assertEqual(observed_at.utcoffset(), timedelta(hours=1))
        self.assertEqual(
            observed_at.astimezone(UTC) - event_at.astimezone(UTC),
            timedelta(minutes=15),
        )

        self.assertEqual(
            DEFAULT_EVENT_REACTION_MONITORING_PROFILE.interval_for(
                event_at=event_at,
                observed_at=observed_at,
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
