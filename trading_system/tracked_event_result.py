from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from trading_system.tracked_event_repository import TrackedEventReactionRecord


@dataclass(frozen=True)
class TrackedEventLatestReaction:
    """Exact persisted values from the latest observed reaction row.

    This is deliberately observation-only: it does not classify the event as
    good/bad, profitable/unprofitable, or create any trading signal. It only
    provides one deterministic read model for later API/mobile presentation.
    """

    interval_minutes: int
    candle_start: datetime
    reference_price: Decimal
    close_price: Decimal
    return_pct: Decimal
    direction: str
    evolution: str
    observed_at: datetime


def latest_tracked_event_reaction(
    reactions: Iterable[TrackedEventReactionRecord],
) -> TrackedEventLatestReaction | None:
    """Return the row whose persisted candle ends latest in market time.

    Stage changes can make a longer candle start before the latest shorter
    candle while still closing later. Rank by candle end first, then resolve
    equal-end ties deterministically by observed_at, candle_start and
    interval_minutes. No persisted numeric values are rounded or recomputed.
    """

    latest = max(
        reactions,
        key=lambda row: (
            row.candle_start + timedelta(minutes=row.interval_minutes),
            row.observed_at,
            row.candle_start,
            row.interval_minutes,
        ),
        default=None,
    )
    if latest is None:
        return None
    return TrackedEventLatestReaction(
        interval_minutes=latest.interval_minutes,
        candle_start=latest.candle_start,
        reference_price=latest.reference_price,
        close_price=latest.close_price,
        return_pct=latest.return_pct,
        direction=latest.direction,
        evolution=latest.evolution,
        observed_at=latest.observed_at,
    )
