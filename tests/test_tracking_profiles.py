from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from trading_system.tracking_profiles import (
    MAX_TRACKING_PROFILE_SPECS_LENGTH,
    TrackedInstrumentProfile,
    TrackingProfileType,
    create_tracking_profile,
    update_tracking_profile,
)


def test_create_profile_normalises_specs_and_preserves_identity() -> None:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    profile = create_tracking_profile(
        tracked_instrument_id=" abc123 ",
        profile_type=TrackingProfileType.FUTURE_TECH,
        specs="  AI photonics, GaN, SiC  ",
        now=now,
    )

    assert profile.tracked_instrument_id == "abc123"
    assert profile.profile_type is TrackingProfileType.FUTURE_TECH
    assert profile.specs == "AI photonics, GaN, SiC"
    assert profile.enabled is True
    assert profile.created_at == now
    assert profile.updated_at == now
    assert profile.key == ("abc123", TrackingProfileType.FUTURE_TECH)


def test_same_instrument_can_have_multiple_profile_types() -> None:
    earnings = create_tracking_profile(
        tracked_instrument_id="abc123",
        profile_type=TrackingProfileType.EARNINGS,
    )
    trend = create_tracking_profile(
        tracked_instrument_id="abc123",
        profile_type=TrackingProfileType.TREND,
    )

    assert earnings.key != trend.key


def test_update_profile_changes_only_configuration_and_preserves_identity() -> None:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    profile = create_tracking_profile(
        tracked_instrument_id="abc123",
        profile_type=TrackingProfileType.TREND,
        specs="Watch correction",
        now=now,
    )

    updated = update_tracking_profile(
        profile,
        specs="Watch relative-strength reversal",
        enabled=False,
        now=now + timedelta(minutes=5),
    )

    assert updated.profile_id == profile.profile_id
    assert updated.tracked_instrument_id == profile.tracked_instrument_id
    assert updated.profile_type == profile.profile_type
    assert updated.created_at == profile.created_at
    assert updated.updated_at == now + timedelta(minutes=5)
    assert updated.specs == "Watch relative-strength reversal"
    assert updated.enabled is False
    assert not hasattr(updated, "event_id")
    assert not hasattr(updated, "strategy")
    assert not hasattr(updated, "risk")
    assert not hasattr(updated, "broker")


def test_idempotent_update_returns_same_profile() -> None:
    profile = create_tracking_profile(
        tracked_instrument_id="abc123",
        profile_type=TrackingProfileType.EARNINGS,
        specs="Read earnings release",
    )

    assert update_tracking_profile(
        profile,
        specs="Read earnings release",
        enabled=True,
    ) is profile


def test_blank_tracked_instrument_id_fails_closed() -> None:
    with pytest.raises(ValueError, match="tracked_instrument_id is required"):
        create_tracking_profile(
            tracked_instrument_id="   ",
            profile_type=TrackingProfileType.TREND,
        )


def test_unsupported_profile_type_fails_closed_on_direct_construction() -> None:
    with pytest.raises(ValueError, match="unsupported tracking profile type"):
        TrackedInstrumentProfile(
            tracked_instrument_id="abc123",
            profile_type="news",  # type: ignore[arg-type]
        )


def test_replace_revalidates_profile_type() -> None:
    profile = create_tracking_profile(
        tracked_instrument_id="abc123",
        profile_type=TrackingProfileType.TREND,
    )

    with pytest.raises(ValueError, match="unsupported tracking profile type"):
        replace(profile, profile_type="news")


def test_specs_length_is_bounded() -> None:
    with pytest.raises(ValueError, match="specs must be at most"):
        create_tracking_profile(
            tracked_instrument_id="abc123",
            profile_type=TrackingProfileType.FUTURE_TECH,
            specs="x" * (MAX_TRACKING_PROFILE_SPECS_LENGTH + 1),
        )
