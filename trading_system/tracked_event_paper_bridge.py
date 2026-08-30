from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable

import pandas as pd

from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.models import ComponentAssessment, EventExpectation, PortfolioState
from trading_system.pipeline import PaperTradingPipeline
from trading_system.post_release_paper import PostReleasePaperResult, run_post_release_paper
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
)


@dataclass(frozen=True)
class TrackedEventPriceConfirmation:
    """Canonical first-live-candle confirmation for one tracked earnings event."""

    tracked_market_event_id: str
    release_event_id: str
    tracked_instrument_id: str
    instrument: str
    market: str
    candle_start: datetime
    reference_price: Decimal
    close_price: Decimal
    return_pct: Decimal
    direction: str


def canonical_release_event_id(event: PersistentTrackedEvent) -> str:
    """Return the release/expectation identity owned by this tracked event."""
    if event.calendar_event_id:
        return f"calendar:{event.calendar_event_id}"
    return f"tracked:{event.event_id}"


def build_tracked_event_price_confirmation(
    *,
    event: PersistentTrackedEvent,
    expectation: EventExpectation,
    reactions: Iterable[TrackedEventReactionRecord],
) -> TrackedEventPriceConfirmation | None:
    """Validate and select the persisted first complete post-event 1m reaction.

    Missing runtime evidence returns ``None`` so orchestration can keep waiting.
    Identity or persisted-reference contradictions raise ``ValueError`` and must
    fail closed rather than silently falling back to another price source.
    """
    if event.kind.strip().lower() != "earnings":
        raise ValueError("tracked event is not an earnings event")

    release_event_id = canonical_release_event_id(event)
    if expectation.event_id != release_event_id:
        raise ValueError("expectation identity differs from tracked event")
    if expectation.instrument.strip().upper() != event.instrument.strip().upper():
        raise ValueError("expectation instrument differs from tracked event")

    anchor = event.reaction_anchor_at
    reference = event.reference_price
    if anchor is None or reference is None:
        return None
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("tracked reaction anchor must be timezone-aware")
    if reference <= 0 or not reference.is_finite():
        raise ValueError("tracked event reference price is invalid")

    candidates = [
        reaction
        for reaction in reactions
        if reaction.tracked_market_event_id == event.event_id
        and reaction.interval_minutes == 1
        and reaction.candle_start.astimezone(UTC) == anchor.astimezone(UTC)
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError("tracked event has ambiguous anchored 1m reactions")

    reaction = candidates[0]
    if reaction.candle_start.tzinfo is None or reaction.candle_start.utcoffset() is None:
        raise ValueError("tracked reaction candle_start must be timezone-aware")
    if reaction.observed_at.tzinfo is None or reaction.observed_at.utcoffset() is None:
        raise ValueError("tracked reaction observed_at must be timezone-aware")
    if reaction.candle_start.astimezone(UTC) < event.event_at.astimezone(UTC):
        raise ValueError("tracked reaction precedes event time")
    if reaction.observed_at.astimezone(UTC) < reaction.candle_start.astimezone(UTC):
        raise ValueError("tracked reaction was observed before its candle started")
    if reaction.reference_price != reference:
        raise ValueError("tracked reaction reference differs from event reference")
    if reaction.close_price <= 0 or not reaction.close_price.is_finite():
        raise ValueError("tracked reaction close price is invalid")
    if not reaction.return_pct.is_finite():
        raise ValueError("tracked reaction return is invalid")

    direction = reaction.direction.strip().lower()
    if direction not in {"positive", "negative", "flat"}:
        raise ValueError("tracked reaction direction is invalid")
    if direction == "positive" and reaction.return_pct <= 0:
        raise ValueError("positive tracked reaction has non-positive return")
    if direction == "negative" and reaction.return_pct >= 0:
        raise ValueError("negative tracked reaction has non-negative return")
    if direction == "flat" and abs(reaction.return_pct) > Decimal("0.25"):
        raise ValueError("flat tracked reaction exceeds canonical flat threshold")

    return TrackedEventPriceConfirmation(
        tracked_market_event_id=event.event_id,
        release_event_id=release_event_id,
        tracked_instrument_id=event.tracked_instrument_id,
        instrument=event.instrument,
        market=event.market,
        candle_start=reaction.candle_start,
        reference_price=reaction.reference_price,
        close_price=reaction.close_price,
        return_pct=reaction.return_pct,
        direction=direction,
    )


def run_post_release_paper_from_tracked_event(
    *,
    event: PersistentTrackedEvent,
    expectation: EventExpectation,
    analysis: EventAnalysisPayload,
    reactions: Iterable[TrackedEventReactionRecord],
    portfolio: PortfolioState,
    market_df: pd.DataFrame | None = None,
    technical: ComponentAssessment | None = None,
    market_memory: ComponentAssessment | None = None,
    pipeline: PaperTradingPipeline | None = None,
) -> PostReleasePaperResult:
    """Use canonical persisted live reaction, then the existing paper pipeline.

    This function does not create or mutate tracked events, expectations,
    strategies, risk state, trading tasks, or broker state itself. It only
    validates the tracked-event evidence and delegates the actual decision to
    the existing ``run_post_release_paper`` Strategy -> Risk -> PaperBroker path.
    There is deliberately no daily-bar fallback here.
    """
    confirmation = build_tracked_event_price_confirmation(
        event=event,
        expectation=expectation,
        reactions=reactions,
    )
    if confirmation is None:
        return PostReleasePaperResult(
            "waiting_confirmation",
            "no canonical anchored 1m tracked reaction yet",
        )
    if confirmation.direction == "flat":
        return PostReleasePaperResult(
            "waiting_confirmation",
            f"tracked price reaction is flat: {float(confirmation.return_pct):+.2f}%",
        )

    return run_post_release_paper(
        expectation=expectation,
        analysis=analysis,
        portfolio=portfolio,
        market_df=market_df,
        technical=technical,
        market_memory=market_memory,
        pipeline=pipeline,
        confirmed_reaction_pct=float(confirmation.return_pct),
    )
