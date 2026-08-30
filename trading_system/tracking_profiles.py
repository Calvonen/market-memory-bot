from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


MAX_TRACKING_PROFILE_SPECS_LENGTH = 4000


class TrackingProfileType(str, Enum):
    """Initial user-selected reasons for tracking an instrument."""

    EARNINGS = "earnings"
    TREND = "trend"
    FUTURE_TECH = "future_tech"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalise_specs(value: str) -> str:
    specs = value.strip()
    if len(specs) > MAX_TRACKING_PROFILE_SPECS_LENGTH:
        raise ValueError(
            f"specs must be at most {MAX_TRACKING_PROFILE_SPECS_LENGTH} characters"
        )
    return specs


@dataclass(frozen=True)
class TrackedInstrumentProfile:
    """Persistable tracking intent attached to one canonical tracked instrument.

    A profile is descriptive configuration only. Creating or changing one must
    not create a tracked market event, strategy/risk decision, broker action, or
    trade.
    """

    tracked_instrument_id: str
    profile_type: TrackingProfileType
    specs: str = ""
    enabled: bool = True
    profile_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        tracked_instrument_id = self.tracked_instrument_id.strip()
        if not tracked_instrument_id:
            raise ValueError("tracked_instrument_id is required")

        try:
            profile_type = TrackingProfileType(self.profile_type)
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported tracking profile type") from exc

        object.__setattr__(self, "tracked_instrument_id", tracked_instrument_id)
        object.__setattr__(self, "profile_type", profile_type)
        object.__setattr__(self, "specs", _normalise_specs(self.specs))

    @property
    def key(self) -> tuple[str, TrackingProfileType]:
        return (self.tracked_instrument_id, self.profile_type)


def create_tracking_profile(
    *,
    tracked_instrument_id: str,
    profile_type: TrackingProfileType,
    specs: str = "",
    now: datetime | None = None,
) -> TrackedInstrumentProfile:
    timestamp = now or _utc_now()
    return TrackedInstrumentProfile(
        tracked_instrument_id=tracked_instrument_id,
        profile_type=profile_type,
        specs=specs,
        created_at=timestamp,
        updated_at=timestamp,
    )


def update_tracking_profile(
    profile: TrackedInstrumentProfile,
    *,
    specs: str | None = None,
    enabled: bool | None = None,
    now: datetime | None = None,
) -> TrackedInstrumentProfile:
    next_specs = profile.specs if specs is None else _normalise_specs(specs)
    next_enabled = profile.enabled if enabled is None else enabled

    if next_specs == profile.specs and next_enabled is profile.enabled:
        return profile

    return replace(
        profile,
        specs=next_specs,
        enabled=next_enabled,
        updated_at=now or _utc_now(),
    )
