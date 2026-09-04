from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import pandas as pd

from market_memory.data import fetch_ohlcv
from market_memory.indicators import add_indicators
from market_memory.pivots import detect_pivots
from market_memory.similarity import find_best_matches
from trading_system.market_memory_bridge import (
    build_market_memory_assessment,
    build_technical_assessment,
)
from trading_system.market_reaction import DEFAULT_FLAT_THRESHOLD_PCT
from trading_system.market_session_profile import (
    GROUNDED_MARKET_SESSION_PROFILES,
    resolve_market_session_profile,
    resolve_provider_symbol,
)
from trading_system.models import (
    ComponentAssessment,
    Direction,
    EventExpectation,
    PortfolioState,
    StrategyInputs,
    TradeLevels,
)
from trading_system.pipeline import PaperTradingPipeline
from trading_system.post_release_paper import PostReleasePaperResult
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
)


_OPENING_WINDOW = timedelta(minutes=30)
_CANONICAL_REFERENCE_KIND = "etoro_last_execution_pre_event_snapshot"


@dataclass(frozen=True)
class MarketOpenPattern:
    direction: Direction
    setup: ComponentAssessment
    confirmation: ComponentAssessment
    reaction_pct: Decimal
    execution_price: Decimal


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _direction(value: Decimal) -> str:
    if value > DEFAULT_FLAT_THRESHOLD_PCT:
        return "positive"
    if value < -DEFAULT_FLAT_THRESHOLD_PCT:
        return "negative"
    return "flat"


def _validate_reference_provenance(event: PersistentTrackedEvent) -> None:
    if event.reference_price is None or not event.reference_price.is_finite() or event.reference_price <= 0:
        raise ValueError("market-open reference price is invalid")
    if event.reference_captured_at is None or not _is_aware(event.reference_captured_at):
        raise ValueError("market-open reference capture timestamp is missing or naive")
    if event.reference_captured_at.astimezone(UTC) > event.event_at.astimezone(UTC):
        raise ValueError("market-open reference was captured after market open")
    if event.reference_kind != _CANONICAL_REFERENCE_KIND:
        raise ValueError("market-open reference kind is not canonical pre-event snapshot")


def _validated_opening_reactions(
    *,
    event: PersistentTrackedEvent,
    reactions: tuple[TrackedEventReactionRecord, ...],
) -> tuple[TrackedEventReactionRecord, ...]:
    if event.kind.strip().lower() != "market_open":
        raise ValueError("tracked event is not a market-open event")
    if not _is_aware(event.event_at):
        raise ValueError("market-open event time must be timezone-aware")
    _validate_reference_provenance(event)
    if event.reaction_anchor_at is None:
        return ()
    if not _is_aware(event.reaction_anchor_at):
        raise ValueError("market-open reaction anchor must be timezone-aware")

    market_open = event.event_at.astimezone(UTC)
    anchor = event.reaction_anchor_at.astimezone(UTC)
    if anchor != market_open:
        raise ValueError("market-open reaction anchor does not match grounded market open")

    end = anchor + _OPENING_WINDOW
    selected: list[TrackedEventReactionRecord] = []
    seen_starts: set[datetime] = set()
    for reaction in reactions:
        if reaction.tracked_market_event_id != event.event_id or reaction.interval_minutes != 1:
            continue
        if not _is_aware(reaction.candle_start) or not _is_aware(reaction.observed_at):
            raise ValueError("market-open reaction timestamps must be timezone-aware")
        candle_start = reaction.candle_start.astimezone(UTC)
        if candle_start < anchor or candle_start >= end:
            continue
        if candle_start in seen_starts:
            raise ValueError("market-open reactions contain duplicate 1m candle starts")
        seen_starts.add(candle_start)
        if reaction.reference_price != event.reference_price:
            raise ValueError("market-open reaction reference differs from event reference")
        if reaction.close_price <= 0 or not reaction.close_price.is_finite():
            raise ValueError("market-open reaction close is invalid")
        if not reaction.return_pct.is_finite():
            raise ValueError("market-open reaction return is invalid")
        canonical_return = (
            (reaction.close_price - event.reference_price) / event.reference_price
        ) * Decimal("100")
        if reaction.return_pct != canonical_return:
            raise ValueError("market-open reaction return differs from stored prices")
        if reaction.direction.strip().lower() != _direction(canonical_return):
            raise ValueError("market-open reaction direction differs from canonical return")
        complete_at = candle_start + timedelta(minutes=1)
        if reaction.observed_at.astimezone(UTC) < complete_at:
            raise ValueError("market-open reaction was observed before candle completion")
        selected.append(reaction)

    ordered = tuple(sorted(selected, key=lambda row: row.candle_start.astimezone(UTC)))
    if ordered and ordered[0].candle_start.astimezone(UTC) != market_open:
        raise ValueError("market-open opening evidence misses the first grounded 1m candle")
    return ordered


def _previous_session_was_down(event: PersistentTrackedEvent) -> bool:
    context = event.pre_event_market_context
    if not isinstance(context, dict):
        return False
    raw = context.get("close_to_close_return_pct")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return False
    return value.is_finite() and value < 0


def detect_market_open_pattern(
    *,
    event: PersistentTrackedEvent,
    reactions: tuple[TrackedEventReactionRecord, ...],
) -> MarketOpenPattern | None:
    """Detect a reviewed bearish-open reversal or failed-bounce continuation.

    The previous session is context only and contributes no directional score.
    Directional Strategy evidence comes exclusively from complete persisted 1m
    opening reactions. At least three reactions are required so a single print
    cannot create both setup and confirmation evidence. The latest accepted
    reaction must itself confirm the selected direction; later contradictory
    evidence invalidates an earlier signal instead of being ignored.
    """
    rows = _validated_opening_reactions(event=event, reactions=reactions)
    if len(rows) < 3 or not _previous_session_was_down(event):
        return None

    first = rows[0]
    latest = rows[-1]
    if _direction(first.return_pct) != "negative":
        return None

    positives = [row for row in rows[1:] if _direction(row.return_pct) == "positive"]
    if _direction(latest.return_pct) == "positive" and len(positives) >= 2:
        first_positive = positives[0]
        if (
            latest.candle_start > first_positive.candle_start
            and latest.return_pct >= first_positive.return_pct
        ):
            return MarketOpenPattern(
                direction=Direction.LONG,
                setup=ComponentAssessment(
                    "fundamental",
                    Direction.LONG,
                    35,
                    35,
                    ("Market-open setup: completed 1m reactions reversed from opening weakness to positive territory.",),
                ),
                confirmation=ComponentAssessment(
                    "catalyst",
                    Direction.LONG,
                    25,
                    25,
                    ("Market-open confirmation: the latest completed 1m reaction held positive and did not give back the first reclaim.",),
                ),
                reaction_pct=latest.return_pct,
                execution_price=latest.close_price,
            )

    if _direction(latest.return_pct) != "negative":
        return None
    for bounce_index in range(1, len(rows) - 1):
        bounce = rows[bounce_index]
        if bounce.return_pct <= first.return_pct:
            continue
        if latest.return_pct >= bounce.return_pct:
            continue
        return MarketOpenPattern(
            direction=Direction.SHORT,
            setup=ComponentAssessment(
                "fundamental",
                Direction.SHORT,
                35,
                35,
                ("Market-open setup: the first completed 1m reaction confirmed opening weakness.",),
            ),
            confirmation=ComponentAssessment(
                "catalyst",
                Direction.SHORT,
                25,
                25,
                ("Market-open confirmation: a completed bounce attempt failed and the latest 1m reaction rolled back negative.",),
            ),
            reaction_pct=latest.return_pct,
            execution_price=latest.close_price,
        )
    return None


def _levels_from_market(
    df: pd.DataFrame,
    direction: Direction,
    execution_price: Decimal,
) -> TradeLevels:
    if not execution_price.is_finite() or execution_price <= 0:
        raise ValueError("market-open execution price is invalid")
    current = df.iloc[-1]
    entry = float(execution_price)
    atr_pct = max(float(current["atr_pct"]), 0.0)
    risk_distance = max(entry * (atr_pct / 100.0), entry * 0.02)
    if direction is Direction.LONG:
        return TradeLevels(entry=entry, stop=entry - risk_distance, target_1=entry + 2.0 * risk_distance)
    return TradeLevels(entry=entry, stop=entry + risk_distance, target_1=entry - 2.0 * risk_distance)


def _provider_symbol(event: PersistentTrackedEvent) -> str:
    if not event.resolved_etoro_market:
        raise ValueError("market-open resolved eToro market is missing")
    if not event.resolved_etoro_symbol:
        raise ValueError("market-open resolved eToro symbol is missing")
    resolved_symbol = event.resolved_etoro_symbol.strip().upper()
    canonical_symbol = event.instrument.strip().upper()
    if not canonical_symbol or resolved_symbol != canonical_symbol:
        raise ValueError("market-open resolved eToro symbol differs from canonical instrument")
    profile = resolve_market_session_profile(
        event.resolved_etoro_market,
        profiles=GROUNDED_MARKET_SESSION_PROFILES,
    )
    return resolve_provider_symbol(resolved_symbol, profile=profile)


def run_market_open_paper(
    *,
    event: PersistentTrackedEvent,
    expectation: EventExpectation,
    reactions: tuple[TrackedEventReactionRecord, ...],
    portfolio: PortfolioState,
    market_df: pd.DataFrame | None = None,
    technical: ComponentAssessment | None = None,
    market_memory: ComponentAssessment | None = None,
    pipeline: PaperTradingPipeline | None = None,
) -> PostReleasePaperResult:
    """Run market-open evidence through the existing Strategy/Risk/PAPER pipeline."""
    pattern = detect_market_open_pattern(event=event, reactions=reactions)
    if pattern is None:
        return PostReleasePaperResult(
            "waiting_confirmation",
            "market-open pattern not confirmed from complete opening 1m reactions",
        )

    if market_df is None:
        market_df = add_indicators(fetch_ohlcv(_provider_symbol(event), period="5y", interval="1d"))
    if market_df.empty:
        return PostReleasePaperResult("waiting_confirmation", "no market data")
    if portfolio.spread_pct is None:
        return PostReleasePaperResult("waiting_confirmation", "paper spread assumption unavailable")
    if portfolio.volatility_pct is None:
        portfolio = replace(portfolio, volatility_pct=float(market_df["atr_pct"].iloc[-1]))

    if technical is None:
        technical = build_technical_assessment(market_df, pattern.direction)
    if market_memory is None:
        pivots = detect_pivots(market_df)
        matches = find_best_matches(market_df, pivots)
        market_memory = build_market_memory_assessment(matches, pattern.direction)

    completed = (pattern.setup, pattern.confirmation)
    if technical.direction is not pattern.direction:
        return PostReleasePaperResult(
            "waiting_confirmation",
            "technical confirmation is not aligned with market-open pattern",
            completed_components=completed,
        )
    if market_memory.direction is not pattern.direction:
        return PostReleasePaperResult(
            "waiting_confirmation",
            "market-memory confirmation is not aligned with market-open pattern",
            completed_components=completed,
        )

    news = ComponentAssessment(
        "news_sentiment",
        Direction.NO_TRADE,
        0,
        10,
        ("No separate news score in market-open execution path.",),
    )
    inputs = StrategyInputs(
        instrument=expectation.instrument,
        fundamental=pattern.setup,
        catalyst=pattern.confirmation,
        technical=technical,
        market_memory=market_memory,
        news_sentiment=news,
        source_event_id=expectation.event_id,
        invalidation=(
            "Opening pattern no longer matches the persisted complete 1m reaction sequence.",
            "Technical or Market Memory no longer confirms the same direction.",
        ),
    )
    result = (pipeline or PaperTradingPipeline()).run(
        inputs,
        _levels_from_market(market_df, pattern.direction, pattern.execution_price),
        portfolio,
    )
    if result.order is None:
        return PostReleasePaperResult(
            "waiting_confirmation",
            f"strategy/risk did not approve: {result.strategy.direction.value}/{result.proposal.risk.status.value}",
            result,
            completed,
        )
    return PostReleasePaperResult(
        "paper_executed",
        f"{result.order.direction.value} {result.order.quantity} {result.order.instrument} {result.order.status}",
        result,
        completed,
    )
