from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from trading_system.market_reaction import DEFAULT_FLAT_THRESHOLD_PCT
from trading_system.reaction_monitoring_profile import (
    DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    ReactionMonitoringProfile,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
)


_INITIAL_OBSERVATION_WINDOW = timedelta(minutes=30)


def _canonical_direction(return_pct: Decimal) -> str:
    if return_pct > DEFAULT_FLAT_THRESHOLD_PCT:
        return "positive"
    if return_pct < -DEFAULT_FLAT_THRESHOLD_PCT:
        return "negative"
    return "flat"


def canonical_post_release_reaction_evidence(
    *,
    event: PersistentTrackedEvent,
    reactions: Iterable[TrackedEventReactionRecord],
    profile: ReactionMonitoringProfile = DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
) -> tuple[TrackedEventReactionRecord, ...]:
    """Return deterministic persisted reaction evidence after the first 30 minutes.

    The tracked-event worker already persists only the monitoring profile's active
    candle interval. This selector independently re-validates that invariant and
    the persisted price/return/direction values at the PAPER-consumption boundary
    so stale, legacy or contradictory rows cannot silently become confirmation
    evidence.

    This function does not authorize PAPER execution or choose trade direction.
    It only exposes the canonical persisted post-observation stream for later
    confirmation policy.
    """
    anchor = event.reaction_anchor_at
    if anchor is None:
        return ()
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("tracked reaction anchor must be timezone-aware")

    reference = event.reference_price
    if reference is None or not reference.is_finite() or reference <= 0:
        raise ValueError("tracked event reference price is missing or invalid")

    anchor_utc = anchor.astimezone(UTC)
    post_observation_start = anchor_utc + _INITIAL_OBSERVATION_WINDOW
    selected: list[tuple[datetime, TrackedEventReactionRecord]] = []
    seen_keys: set[tuple[int, datetime]] = set()

    for reaction in reactions:
        if reaction.tracked_market_event_id != event.event_id:
            continue
        if reaction.candle_start.tzinfo is None or reaction.candle_start.utcoffset() is None:
            raise ValueError("tracked reaction candle_start must be timezone-aware")
        if reaction.observed_at.tzinfo is None or reaction.observed_at.utcoffset() is None:
            raise ValueError("tracked reaction observed_at must be timezone-aware")
        if reaction.interval_minutes <= 0:
            raise ValueError("tracked reaction interval must be positive")

        candle_start_utc = reaction.candle_start.astimezone(UTC)
        candle_complete_at = candle_start_utc + timedelta(minutes=reaction.interval_minutes)
        if candle_start_utc < anchor_utc:
            raise ValueError("persisted post-release reaction starts before reaction anchor")
        if reaction.observed_at.astimezone(UTC) != candle_complete_at:
            raise ValueError("tracked reaction observation time must equal candle completion")
        if candle_complete_at <= post_observation_start:
            continue

        active_interval = profile.interval_for(
            event_at=anchor_utc,
            observed_at=candle_complete_at,
        )
        if active_interval != reaction.interval_minutes:
            raise ValueError(
                "persisted post-release reaction interval differs from canonical monitoring profile"
            )

        if reaction.reference_price != reference:
            raise ValueError("persisted post-release reaction reference differs from event reference")
        if not reaction.close_price.is_finite() or reaction.close_price <= 0:
            raise ValueError("persisted post-release reaction close price is invalid")
        if not reaction.return_pct.is_finite():
            raise ValueError("persisted post-release reaction return is invalid")
        canonical_return = ((reaction.close_price - reference) / reference) * Decimal("100")
        if reaction.return_pct != canonical_return:
            raise ValueError("persisted post-release reaction return differs from stored prices")
        if reaction.direction.strip().lower() != _canonical_direction(canonical_return):
            raise ValueError("persisted post-release reaction direction differs from canonical return")

        key = (reaction.interval_minutes, candle_start_utc)
        if key in seen_keys:
            raise ValueError("persisted post-release reaction evidence is ambiguous")
        seen_keys.add(key)
        selected.append((candle_complete_at, reaction))

    selected.sort(key=lambda item: (item[0], item[1].interval_minutes))
    return tuple(reaction for _, reaction in selected)
