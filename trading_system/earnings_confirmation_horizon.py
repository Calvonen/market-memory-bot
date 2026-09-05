from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from trading_system.market_reaction import DEFAULT_FLAT_THRESHOLD_PCT
from trading_system.reaction_monitoring_profile import (
    DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
)
from trading_system.tracked_event_config import TRACKING_CONFIG_SCHEMA_VERSION
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
)


@dataclass(frozen=True)
class EarningsConfirmationHorizonDecision:
    complete: bool
    reason: str


def _canonical_direction(return_pct: Decimal) -> str:
    if return_pct > DEFAULT_FLAT_THRESHOLD_PCT:
        return "positive"
    if return_pct < -DEFAULT_FLAT_THRESHOLD_PCT:
        return "negative"
    return "flat"


def _latest_aligned_completion(
    *,
    cutoff: datetime,
    interval_minutes: int,
    inclusive: bool,
) -> datetime:
    interval_seconds = interval_minutes * 60
    cutoff_seconds = cutoff.timestamp()
    boundary_seconds = math.floor(cutoff_seconds / interval_seconds) * interval_seconds
    completion = datetime.fromtimestamp(boundary_seconds, UTC)
    if completion > cutoff or (completion == cutoff and not inclusive):
        completion -= timedelta(minutes=interval_minutes)
    return completion


def _terminal_persistable_completion(
    *,
    anchor: datetime,
    monitor_hours: float,
) -> tuple[datetime, int] | None:
    """Return the latest canonical candle production can persist by the cutoff."""
    anchor_utc = anchor.astimezone(UTC)
    horizon_end = anchor_utc + timedelta(hours=monitor_hours)
    stages = DEFAULT_EVENT_REACTION_MONITORING_PROFILE.stages
    candidates: list[tuple[datetime, int]] = []

    for index, stage in enumerate(stages):
        interval_minutes = stage.interval_minutes
        stage_lower = anchor_utc + stage.start_after
        next_stage = stages[index + 1] if index + 1 < len(stages) else None

        # Exact stage boundaries belong to the preceding cadence. Cap this
        # stage's terminal completion at the next stage boundary so a short gap
        # before the next cadence can emit still retains the preceding-stage
        # terminal evidence.
        canonical_upper = horizon_end
        if next_stage is not None:
            canonical_upper = min(
                canonical_upper,
                anchor_utc + next_stage.start_after,
            )

        if interval_minutes == 1:
            # The one-minute builder can emit the prior minute exactly when the
            # next minute starts, so a 1m candle completing at the cutoff itself
            # is observable/persistable.
            persistable_upper = horizon_end
        else:
            # Multi-minute buckets consume already-closed 1m candles. A bucket
            # that completes at T is not emitted until the 1m candle starting at
            # T closes one minute later, so reserve that emission lead.
            persistable_upper = horizon_end - timedelta(minutes=1)

        completion = _latest_aligned_completion(
            cutoff=min(canonical_upper, persistable_upper),
            interval_minutes=interval_minutes,
            inclusive=True,
        )

        # A completion exactly at this stage's start boundary belongs to the
        # preceding stage; the first stage instead only needs to be after the
        # event anchor itself.
        if completion <= stage_lower:
            continue

        active_interval = DEFAULT_EVENT_REACTION_MONITORING_PROFILE.interval_for(
            event_at=anchor_utc,
            observed_at=completion,
        )
        if active_interval != interval_minutes:
            continue
        candidates.append((completion, interval_minutes))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])


def _validate_terminal_reaction(
    *,
    event: PersistentTrackedEvent,
    reaction: TrackedEventReactionRecord,
    terminal_completion: datetime,
    interval_minutes: int,
) -> None:
    anchor = event.reaction_anchor_at
    reference = event.reference_price
    if anchor is None or reference is None:
        raise ValueError("tracked event terminal evidence lacks canonical anchor/reference")
    if not reference.is_finite() or reference <= 0:
        raise ValueError("tracked event reference price is missing or invalid")
    if reaction.candle_start.tzinfo is None or reaction.candle_start.utcoffset() is None:
        raise ValueError("tracked reaction candle_start must be timezone-aware")
    if reaction.observed_at.tzinfo is None or reaction.observed_at.utcoffset() is None:
        raise ValueError("tracked reaction observed_at must be timezone-aware")
    if reaction.interval_minutes != interval_minutes:
        raise ValueError("terminal reaction interval differs from canonical monitoring profile")

    candle_start_utc = reaction.candle_start.astimezone(UTC)
    candle_complete_at = candle_start_utc + timedelta(minutes=reaction.interval_minutes)
    if candle_complete_at != terminal_completion:
        raise ValueError("terminal reaction does not match canonical completion boundary")
    if reaction.observed_at.astimezone(UTC) != candle_complete_at:
        raise ValueError("tracked reaction observation time must equal candle completion")

    active_interval = DEFAULT_EVENT_REACTION_MONITORING_PROFILE.interval_for(
        event_at=anchor.astimezone(UTC),
        observed_at=candle_complete_at,
    )
    if active_interval != reaction.interval_minutes:
        raise ValueError("terminal reaction interval differs from canonical monitoring profile")
    if reaction.reference_price != reference:
        raise ValueError("terminal reaction reference differs from event reference")
    if not reaction.close_price.is_finite() or reaction.close_price <= 0:
        raise ValueError("terminal reaction close price is invalid")
    if not reaction.return_pct.is_finite():
        raise ValueError("terminal reaction return is invalid")

    canonical_return = ((reaction.close_price - reference) / reference) * Decimal("100")
    if reaction.return_pct != canonical_return:
        raise ValueError("terminal reaction return differs from stored prices")
    if reaction.direction.strip().lower() != _canonical_direction(canonical_return):
        raise ValueError("terminal reaction direction differs from canonical return")


def evaluate_earnings_confirmation_horizon(
    *,
    event: PersistentTrackedEvent,
    reactions: Iterable[TrackedEventReactionRecord],
) -> EarningsConfirmationHorizonDecision:
    """Prove persisted earnings confirmation-horizon completion from evidence.

    Wall-clock time alone never completes the horizon. Completion requires the
    terminal canonical candle that production can actually persist by its
    configured cutoff, including the nested 1m -> multi-minute emission lead.
    """
    snapshot = event.tracking_config_snapshot
    if snapshot is None:
        return EarningsConfirmationHorizonDecision(False, "tracking_snapshot_missing")
    if not isinstance(snapshot, dict):
        raise ValueError("tracked event monitoring snapshot is invalid")

    raw_schema_version = snapshot.get("schema_version")
    if (
        isinstance(raw_schema_version, bool)
        or not isinstance(raw_schema_version, int)
        or raw_schema_version != TRACKING_CONFIG_SCHEMA_VERSION
    ):
        raise ValueError("tracked event monitoring snapshot schema is unsupported")

    raw_monitor_hours = snapshot.get("monitor_hours")
    if isinstance(raw_monitor_hours, bool) or not isinstance(raw_monitor_hours, (int, float)):
        raise ValueError("tracked event monitoring snapshot monitor_hours is invalid")
    monitor_hours = float(raw_monitor_hours)
    if not math.isfinite(monitor_hours) or monitor_hours <= 0:
        raise ValueError("tracked event monitoring snapshot monitor_hours is invalid")

    expected_stages = [
        {
            "start_after_minutes": int(stage.start_after / timedelta(minutes=1)),
            "interval_minutes": stage.interval_minutes,
        }
        for stage in DEFAULT_EVENT_REACTION_MONITORING_PROFILE.stages
    ]
    raw_stages = snapshot.get("reaction_stages")
    if not isinstance(raw_stages, list) or len(raw_stages) != len(expected_stages):
        raise ValueError("tracked event monitoring snapshot differs from canonical reaction profile")
    for raw_stage, expected_stage in zip(raw_stages, expected_stages, strict=True):
        if not isinstance(raw_stage, dict) or set(raw_stage) != set(expected_stage):
            raise ValueError("tracked event monitoring snapshot differs from canonical reaction profile")
        for field, expected_value in expected_stage.items():
            value = raw_stage.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value != expected_value:
                raise ValueError("tracked event monitoring snapshot differs from canonical reaction profile")

    anchor = event.reaction_anchor_at
    if anchor is None:
        return EarningsConfirmationHorizonDecision(False, "reaction_anchor_missing")
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("tracked reaction anchor must be timezone-aware")

    terminal = _terminal_persistable_completion(
        anchor=anchor,
        monitor_hours=monitor_hours,
    )
    if terminal is None:
        return EarningsConfirmationHorizonDecision(False, "horizon_evidence_incomplete")
    terminal_completion, interval_minutes = terminal
    terminal_start = terminal_completion - timedelta(minutes=interval_minutes)

    matches = []
    for reaction in reactions:
        if reaction.tracked_market_event_id != event.event_id:
            continue
        if reaction.interval_minutes != interval_minutes:
            continue
        if reaction.candle_start.tzinfo is None or reaction.candle_start.utcoffset() is None:
            raise ValueError("tracked reaction candle_start must be timezone-aware")
        if reaction.candle_start.astimezone(UTC) == terminal_start:
            matches.append(reaction)

    if len(matches) > 1:
        raise ValueError("terminal reaction evidence is ambiguous")
    if not matches:
        return EarningsConfirmationHorizonDecision(False, "horizon_evidence_incomplete")

    _validate_terminal_reaction(
        event=event,
        reaction=matches[0],
        terminal_completion=terminal_completion,
        interval_minutes=interval_minutes,
    )
    return EarningsConfirmationHorizonDecision(True, "terminal_persistable_reaction")
