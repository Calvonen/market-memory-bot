from __future__ import annotations

from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.models import ComponentAssessment, Direction, StrategyInputs


def _direction(value: str) -> Direction:
    normalized = value.upper()
    if normalized == "BULLISH":
        return Direction.LONG
    if normalized == "BEARISH":
        return Direction.SHORT
    return Direction.NO_TRADE


def event_analysis_components(
    analysis: EventAnalysisPayload,
) -> tuple[ComponentAssessment, ComponentAssessment]:
    """Convert structured AI output into bounded Strategy Engine components.

    AI controls only the evidence score inside the pre-agreed fundamental/catalyst
    budgets. It cannot modify component maxima or any Risk Engine setting.
    """
    fundamental = ComponentAssessment(
        name="fundamental",
        direction=_direction(analysis.fundamental_direction),
        score=min(max(analysis.fundamental_score_0_35, 0), 35),
        max_score=35,
        reasons=tuple(
            analysis.key_positive_surprises
            + analysis.key_negative_surprises
            + analysis.uncertainties
        ),
    )
    catalyst = ComponentAssessment(
        name="catalyst",
        direction=_direction(analysis.catalyst_direction),
        score=min(max(analysis.catalyst_score_0_25, 0), 25),
        max_score=25,
        reasons=tuple(
            [analysis.guidance_summary]
            + analysis.key_positive_surprises
            + analysis.key_negative_surprises
        ),
    )
    return fundamental, catalyst


def build_event_strategy_inputs(
    *,
    instrument: str,
    event_id: str,
    analysis: EventAnalysisPayload,
    technical: ComponentAssessment,
    market_memory: ComponentAssessment,
    news_sentiment: ComponentAssessment,
) -> StrategyInputs:
    fundamental, catalyst = event_analysis_components(analysis)
    return StrategyInputs(
        instrument=instrument,
        fundamental=fundamental,
        catalyst=catalyst,
        technical=technical,
        market_memory=market_memory,
        news_sentiment=news_sentiment,
        source_event_id=event_id,
        invalidation=tuple(analysis.invalidation_flags),
    )
