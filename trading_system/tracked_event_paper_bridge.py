from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable

import pandas as pd

from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.etoro_instrument_resolver import (
    EtoroInstrumentResolver,
    InstrumentResolutionRequest,
)
from trading_system.market_reaction import DEFAULT_FLAT_THRESHOLD_PCT
from trading_system.models import (
    ComponentAssessment,
    EventExpectation,
    PortfolioState,
    TradingMode,
)
from trading_system.pipeline import PaperTradingPipeline
from trading_system.post_release_paper import PostReleasePaperResult, run_post_release_paper
from trading_system.risk import RiskEngine
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
)


_CANONICAL_REFERENCE_KIND = "etoro_last_execution_pre_event_snapshot"


@dataclass(frozen=True)
class CanonicalTradingTaskExecutionContext:
    """Read-only execution authority loaded from the canonical trading task.

    The bridge never creates this authority. Its caller must obtain it from the
    canonical trading-task source and pass the task identity, event identity,
    instrument and explicit execution mode unchanged.
    """

    task_id: str
    source_event_id: str
    instrument: str
    mode: TradingMode
    max_position_value_usd: float | None = None


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


def _normalise_text(value: str) -> str:
    return " ".join(value.strip().upper().split())


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _validate_trading_task_execution(
    trading_task: CanonicalTradingTaskExecutionContext,
    *,
    release_event_id: str,
    instrument: str,
) -> None:
    if not isinstance(trading_task, CanonicalTradingTaskExecutionContext):
        raise ValueError("canonical trading task execution context is required")
    if not trading_task.task_id.strip():
        raise ValueError("canonical trading task id must not be blank")
    if trading_task.source_event_id != release_event_id:
        raise ValueError("canonical trading task event identity differs from tracked event")
    if _normalise_text(trading_task.instrument) != _normalise_text(instrument):
        raise ValueError("canonical trading task instrument differs from tracked event")
    if trading_task.mode is not TradingMode.PAPER:
        raise ValueError("canonical trading task does not explicitly request PAPER execution")
    cap = trading_task.max_position_value_usd
    if cap is not None and (not math.isfinite(float(cap)) or cap <= 0):
        raise ValueError("canonical trading task position cap is invalid")


def _pipeline_with_task_cap(
    pipeline: PaperTradingPipeline | None,
    task: CanonicalTradingTaskExecutionContext,
) -> PaperTradingPipeline | None:
    cap = task.max_position_value_usd
    if cap is None:
        return pipeline

    base = pipeline or PaperTradingPipeline()
    existing_cap = base.risk_engine.config.max_position_value_usd
    effective_cap = float(cap) if existing_cap is None else min(float(cap), float(existing_cap))
    risk_engine = RiskEngine(
        replace(base.risk_engine.config, max_position_value_usd=effective_cap)
    )
    return PaperTradingPipeline(
        strategy_engine=base.strategy_engine,
        risk_engine=risk_engine,
        broker=base.broker,
        journal=base.journal,
        allow_fractional_sizing=base.allow_fractional_sizing,
    )


def _validate_broker_identity(
    event: PersistentTrackedEvent,
    resolver: EtoroInstrumentResolver,
) -> None:
    """Re-resolve eToro identity and prove the persisted reference belongs to it."""
    if event.resolution_armed_at is None or not event.resolution_armed_by:
        raise ValueError("persisted reference eToro identity is not armed")
    if not _is_aware(event.resolution_armed_at):
        raise ValueError("persisted eToro resolution timestamp must be timezone-aware")
    if event.resolution_armed_at.astimezone(UTC) > event.event_at.astimezone(UTC):
        raise ValueError("persisted eToro identity was armed after event time")

    resolved = resolver.resolve(
        InstrumentResolutionRequest(
            instrument=event.instrument,
            company_name=event.company_name,
            market=event.market,
        )
    )
    if resolved is None:
        raise ValueError("eToro instrument resolution failed or was ambiguous")
    if event.resolved_etoro_instrument_id != resolved.instrument_id:
        raise ValueError("persisted reference eToro instrument id differs from current resolution")
    if not event.resolved_etoro_symbol or _normalise_text(event.resolved_etoro_symbol) != _normalise_text(
        resolved.symbol
    ):
        raise ValueError("persisted reference eToro symbol differs from current resolution")
    if not event.resolved_etoro_display_name or _normalise_text(
        event.resolved_etoro_display_name
    ) != _normalise_text(resolved.display_name):
        raise ValueError("persisted reference eToro display name differs from current resolution")
    if not event.resolved_etoro_market or _normalise_text(event.resolved_etoro_market) != _normalise_text(
        resolved.market
    ):
        raise ValueError("persisted reference eToro market differs from current resolution")


def _canonical_direction(return_pct: Decimal) -> str:
    if return_pct > DEFAULT_FLAT_THRESHOLD_PCT:
        return "positive"
    if return_pct < -DEFAULT_FLAT_THRESHOLD_PCT:
        return "negative"
    return "flat"


def build_tracked_event_price_confirmation(
    *,
    event: PersistentTrackedEvent,
    expectation: EventExpectation,
    reactions: Iterable[TrackedEventReactionRecord],
    resolver: EtoroInstrumentResolver,
) -> TrackedEventPriceConfirmation | None:
    """Validate and select the persisted first complete post-event 1m reaction.

    Missing runtime evidence returns ``None`` so orchestration can keep waiting.
    Identity or persisted-reference contradictions raise ``ValueError`` and must
    fail closed rather than silently falling back to another price source.
    """
    if event.kind.strip().lower() != "earnings":
        raise ValueError("tracked event is not an earnings event")
    if not _is_aware(event.event_at):
        raise ValueError("tracked event time must be timezone-aware")

    release_event_id = canonical_release_event_id(event)
    if expectation.event_id != release_event_id:
        raise ValueError("expectation identity differs from tracked event")
    if expectation.instrument.strip().upper() != event.instrument.strip().upper():
        raise ValueError("expectation instrument differs from tracked event")

    anchor = event.reaction_anchor_at
    reference = event.reference_price
    if anchor is None or reference is None:
        return None
    if not _is_aware(anchor):
        raise ValueError("tracked reaction anchor must be timezone-aware")
    if reference <= 0 or not reference.is_finite():
        raise ValueError("tracked event reference price is invalid")
    if event.reference_captured_at is None:
        raise ValueError("tracked event reference capture timestamp is missing")
    if not _is_aware(event.reference_captured_at):
        raise ValueError("tracked event reference capture timestamp must be timezone-aware")
    if event.reference_captured_at.astimezone(UTC) > event.event_at.astimezone(UTC):
        raise ValueError("tracked event reference was captured after event time")
    if event.reference_kind != _CANONICAL_REFERENCE_KIND:
        raise ValueError("tracked event reference kind is not canonical pre-event snapshot")

    _validate_broker_identity(event, resolver)

    candidates: list[TrackedEventReactionRecord] = []
    for reaction in reactions:
        if reaction.tracked_market_event_id != event.event_id or reaction.interval_minutes != 1:
            continue
        if not _is_aware(reaction.candle_start):
            raise ValueError("tracked reaction candle_start must be timezone-aware")
        if reaction.candle_start.astimezone(UTC) == anchor.astimezone(UTC):
            candidates.append(reaction)

    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError("tracked event has ambiguous anchored 1m reactions")

    reaction = candidates[0]
    if not _is_aware(reaction.observed_at):
        raise ValueError("tracked reaction observed_at must be timezone-aware")
    if reaction.candle_start.astimezone(UTC) < event.event_at.astimezone(UTC):
        raise ValueError("tracked reaction precedes event time")
    candle_complete_at = reaction.candle_start.astimezone(UTC) + timedelta(
        minutes=reaction.interval_minutes
    )
    if reaction.observed_at.astimezone(UTC) < candle_complete_at:
        raise ValueError("tracked reaction was observed before its candle completed")
    if reaction.reference_price != reference:
        raise ValueError("tracked reaction reference differs from event reference")
    if reaction.close_price <= 0 or not reaction.close_price.is_finite():
        raise ValueError("tracked reaction close price is invalid")
    if not reaction.return_pct.is_finite():
        raise ValueError("tracked reaction return is invalid")

    canonical_return = ((reaction.close_price - reference) / reference) * Decimal("100")
    if reaction.return_pct != canonical_return:
        raise ValueError("tracked reaction return differs from stored prices")

    direction = reaction.direction.strip().lower()
    canonical_direction = _canonical_direction(canonical_return)
    if direction != canonical_direction:
        raise ValueError("tracked reaction direction differs from canonical return")

    return TrackedEventPriceConfirmation(
        tracked_market_event_id=event.event_id,
        release_event_id=release_event_id,
        tracked_instrument_id=event.tracked_instrument_id,
        instrument=event.instrument,
        market=event.market,
        candle_start=reaction.candle_start,
        reference_price=reaction.reference_price,
        close_price=reaction.close_price,
        return_pct=canonical_return,
        direction=canonical_direction,
    )


def run_post_release_paper_from_tracked_event(
    *,
    event: PersistentTrackedEvent,
    expectation: EventExpectation,
    analysis: EventAnalysisPayload,
    reactions: Iterable[TrackedEventReactionRecord],
    portfolio: PortfolioState,
    resolver: EtoroInstrumentResolver,
    trading_task: CanonicalTradingTaskExecutionContext,
    market_df: pd.DataFrame | None = None,
    technical: ComponentAssessment | None = None,
    market_memory: ComponentAssessment | None = None,
    pipeline: PaperTradingPipeline | None = None,
) -> PostReleasePaperResult:
    """Route canonical tracked evidence through the existing PAPER pipeline.

    Observation evidence alone never authorizes execution. The caller must also
    supply read-only execution authority loaded from the canonical trading task;
    the task must be bound to this event/instrument and explicitly request PAPER.
    The bridge performs no persistence or broker writes itself and has no
    daily-bar fallback for tracked-event confirmation.
    """
    release_event_id = canonical_release_event_id(event)
    _validate_trading_task_execution(
        trading_task,
        release_event_id=release_event_id,
        instrument=event.instrument,
    )
    if expectation.event_id != release_event_id:
        raise ValueError("expectation identity differs from tracked event")
    if expectation.instrument.strip().upper() != event.instrument.strip().upper():
        raise ValueError("expectation instrument differs from tracked event")

    kind = event.kind.strip().lower()
    if kind == "market_open":
        raise ValueError("market_open must use dedicated approved market-open PAPER orchestration")
    if kind != "earnings":
        raise ValueError(f"tracked event kind is not PAPER-supported: {kind or 'blank'}")

    confirmation = build_tracked_event_price_confirmation(
        event=event,
        expectation=expectation,
        reactions=reactions,
        resolver=resolver,
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
        pipeline=_pipeline_with_task_cap(pipeline, trading_task),
        confirmed_reaction_pct=float(confirmation.return_pct),
    )
