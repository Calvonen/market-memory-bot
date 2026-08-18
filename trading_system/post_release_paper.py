from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from market_memory.data import fetch_ohlcv
from market_memory.indicators import add_indicators
from market_memory.pivots import detect_pivots
from market_memory.similarity import find_best_matches
from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.event_strategy_bridge import build_event_strategy_inputs, event_analysis_components
from trading_system.events import compare_event
from trading_system.market_memory_bridge import build_market_memory_assessment, build_technical_assessment
from trading_system.models import (
    ComponentAssessment,
    Direction,
    EventActuals,
    EventExpectation,
    PortfolioState,
    TradeLevels,
)
from trading_system.pipeline import PaperTradingPipeline, PipelineResult


@dataclass(frozen=True)
class PostReleasePaperResult:
    status: str
    message: str
    pipeline: PipelineResult | None = None


def _event_hypothesis(
    expectation: EventExpectation,
    analysis: EventAnalysisPayload,
) -> Direction:
    actuals = EventActuals(
        event_id=expectation.event_id,
        instrument=expectation.instrument,
        published_at=datetime.now(UTC),
        source_url=expectation.source_url or "official-release",
        metrics=analysis.metric_values(),
        guidance_summary=analysis.guidance_summary,
        management_summary=analysis.management_summary,
    )
    comparison = compare_event(expectation, actuals)
    fundamental, catalyst = event_analysis_components(
        analysis,
        expectation=expectation,
        comparison=comparison,
    )
    directional = [
        component.direction
        for component in (fundamental, catalyst)
        if component.direction in {Direction.LONG, Direction.SHORT}
    ]
    if not directional or len(set(directional)) != 1:
        return Direction.NO_TRADE
    return directional[0]


def _price_reaction_pct(df: pd.DataFrame) -> float | None:
    if len(df) < 2:
        return None
    previous = float(df["Close"].iloc[-2])
    current = float(df["Close"].iloc[-1])
    if previous <= 0:
        return None
    return ((current / previous) - 1.0) * 100.0


def _latest_bar_date(df: pd.DataFrame):
    if df.empty:
        return None
    value = df.index[-1]
    return value.date() if hasattr(value, "date") else None


def _levels_from_market(df: pd.DataFrame, direction: Direction) -> TradeLevels:
    current = df.iloc[-1]
    entry = float(current["Close"])
    atr_pct = max(float(current["atr_pct"]), 0.0)
    risk_distance = max(entry * (atr_pct / 100.0), entry * 0.02)
    if direction is Direction.LONG:
        return TradeLevels(
            entry=entry,
            stop=entry - risk_distance,
            target_1=entry + (2.0 * risk_distance),
        )
    return TradeLevels(
        entry=entry,
        stop=entry + risk_distance,
        target_1=entry - (2.0 * risk_distance),
    )


def run_post_release_paper(
    *,
    expectation: EventExpectation,
    analysis: EventAnalysisPayload,
    portfolio: PortfolioState,
    market_df: pd.DataFrame | None = None,
    technical: ComponentAssessment | None = None,
    market_memory: ComponentAssessment | None = None,
    pipeline: PaperTradingPipeline | None = None,
) -> PostReleasePaperResult:
    """Run the event -> confirmation -> Strategy -> Risk -> PaperBroker path.

    This is deliberately fail-closed.  It will not create a paper order until an
    event-day market bar exists, the initial price reaction agrees with the event
    hypothesis, and both technical and market-memory confirmation are directional.
    """
    hypothesis = _event_hypothesis(expectation, analysis)
    if hypothesis is Direction.NO_TRADE:
        return PostReleasePaperResult("waiting_confirmation", "event direction is mixed or neutral")

    if market_df is None:
        market_df = add_indicators(fetch_ohlcv(expectation.instrument, period="5y", interval="1d"))
    if market_df.empty:
        return PostReleasePaperResult("waiting_confirmation", "no market data")

    latest_date = _latest_bar_date(market_df)
    if latest_date is None or latest_date < expectation.scheduled_date:
        return PostReleasePaperResult("waiting_confirmation", "no event-day market bar yet")

    reaction = _price_reaction_pct(market_df)
    if reaction is None:
        return PostReleasePaperResult("waiting_confirmation", "price reaction unavailable")
    if (hypothesis is Direction.LONG and reaction <= 0) or (hypothesis is Direction.SHORT and reaction >= 0):
        return PostReleasePaperResult(
            "waiting_confirmation",
            f"price reaction conflicts with {hypothesis.value}: {reaction:+.2f}%",
        )

    if technical is None:
        technical = build_technical_assessment(market_df, hypothesis)
    if market_memory is None:
        pivots = detect_pivots(market_df)
        matches = find_best_matches(market_df, pivots)
        market_memory = build_market_memory_assessment(matches, hypothesis)

    if technical.direction is not hypothesis:
        return PostReleasePaperResult("waiting_confirmation", "technical confirmation is not aligned")
    if market_memory.direction is not hypothesis:
        return PostReleasePaperResult("waiting_confirmation", "market-memory confirmation is not aligned")

    actuals = EventActuals(
        event_id=expectation.event_id,
        instrument=expectation.instrument,
        published_at=datetime.now(UTC),
        source_url=expectation.source_url or "official-release",
        metrics=analysis.metric_values(),
        guidance_summary=analysis.guidance_summary,
        management_summary=analysis.management_summary,
        price_reaction_pct=reaction,
    )
    comparison = compare_event(expectation, actuals)
    news = ComponentAssessment(
        "news_sentiment",
        Direction.NO_TRADE,
        0,
        10,
        ("No separate news score in release execution path.",),
    )
    inputs = build_event_strategy_inputs(
        instrument=expectation.instrument,
        event_id=expectation.event_id,
        expectation=expectation,
        comparison=comparison,
        analysis=analysis,
        technical=technical,
        market_memory=market_memory,
        news_sentiment=news,
    )
    levels = _levels_from_market(market_df, hypothesis)
    result = (pipeline or PaperTradingPipeline()).run(inputs, levels, portfolio)
    if result.order is None:
        return PostReleasePaperResult(
            "waiting_confirmation",
            f"strategy/risk did not approve: {result.strategy.direction.value}/{result.proposal.risk.status.value}",
            result,
        )
    return PostReleasePaperResult(
        "paper_executed",
        f"{result.order.direction.value} {result.order.quantity} {result.order.instrument} {result.order.status}",
        result,
    )
