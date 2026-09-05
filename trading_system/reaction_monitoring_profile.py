from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


SUPPORTED_REACTION_INTERVALS = frozenset({1, 5, 15})


@dataclass(frozen=True)
class ReactionMonitoringStage:
    start_after: timedelta
    interval_minutes: int

    def __post_init__(self) -> None:
        if self.start_after < timedelta(0):
            raise ValueError("start_after must be non-negative")
        if self.interval_minutes not in SUPPORTED_REACTION_INTERVALS:
            raise ValueError("interval_minutes must be one of 1, 5 or 15")


@dataclass(frozen=True)
class ReactionMonitoringProfile:
    """Select which candle interval should drive event-reaction analysis over time.

    The candle stream itself remains continuous. This profile only chooses which
    already-supported closed-candle interval is the active analysis resolution
    for a given elapsed time after an event.
    """

    stages: tuple[ReactionMonitoringStage, ...]

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("stages must not be empty")
        if self.stages[0].start_after != timedelta(0):
            raise ValueError("first stage must start at event time")

        previous_start: timedelta | None = None
        for stage in self.stages:
            if previous_start is not None and stage.start_after <= previous_start:
                raise ValueError("stage start_after values must be strictly increasing")
            previous_start = stage.start_after

    def interval_for(self, *, event_at: datetime, observed_at: datetime) -> int | None:
        if event_at.tzinfo is None or event_at.utcoffset() is None:
            raise ValueError("event_at must be timezone-aware")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")

        # Convert to UTC before subtracting so the elapsed duration reflects real
        # wall-clock time even when event_at and observed_at straddle a DST
        # transition in a shared local tzinfo.
        elapsed = observed_at.astimezone(timezone.utc) - event_at.astimezone(timezone.utc)
        if elapsed < timedelta(0):
            return None

        selected = self.stages[0].interval_minutes
        for stage in self.stages[1:]:
            # A candle that closes exactly on a stage boundary still belongs to
            # the preceding stage. This lets the final 1m candle of the first
            # 30-minute observation window persist at +30:00 before 5m sampling
            # becomes active immediately afterwards.
            if elapsed <= stage.start_after:
                break
            selected = stage.interval_minutes
        return selected


DEFAULT_EVENT_REACTION_MONITORING_PROFILE = ReactionMonitoringProfile(
    stages=(
        ReactionMonitoringStage(start_after=timedelta(0), interval_minutes=1),
        ReactionMonitoringStage(start_after=timedelta(minutes=30), interval_minutes=5),
        ReactionMonitoringStage(start_after=timedelta(minutes=150), interval_minutes=15),
    )
)
