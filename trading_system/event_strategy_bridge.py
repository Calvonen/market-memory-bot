from __future__ import annotations

from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.models import (
    ComponentAssessment,
    Direction,
    EventComparison,
    EventExpectation,
    StrategyInputs,
)


def _direction(value: str) -> Direction:
    normalized = value.upper()
    if normalized == "BULLISH":
        return Direction.LONG
    if normalized == "BEARISH":
        return Direction.SHORT
    return Direction.NO_TRADE


def _normalized_metric_name(value: str) -> str:
    """Normalize trigger/metric names without weakening canonical AI metric names.

    Event trigger names pre-date the canonical metric vocabulary in a few fixtures
    (for example fy27_operating_profit_gbp_m versus
    fy27_operating_profit_pre_exceptional_gbp_m).  The comparison layer may use
    this narrow alias normalization while AI extraction remains strict/canonical.
    """
    return value.replace("_pre_exceptional", "")


def deterministic_event_components(
    expectation: EventExpectation,
    comparison: EventComparison,
) -> tuple[ComponentAssessment, ComponentAssessment] | None:
    """Turn explicit pre-event bull/bear triggers into bounded base evidence.

    This is deliberately deterministic: an LLM cannot neutralize a released
    canonical metric that crossed a versioned event trigger.  A trigger hit
    contributes a conservative 20/35 fundamental and 20/25 catalyst base; other
    components are still required to clear the Strategy Engine's trade gate.
    """
    metric_by_name = {item.metric: item for item in comparison.metrics}
    hits: list[tuple[Direction, str]] = []

    for trigger_name, threshold in expectation.triggers.items():
        if not isinstance(threshold, (int, float)):
            continue

        if trigger_name.startswith("bull_"):
            direction = Direction.LONG
            metric_key = trigger_name[len("bull_") :]
            crossed = lambda actual: actual >= float(threshold)
            operator = ">="
        elif trigger_name.startswith("bear_"):
            direction = Direction.SHORT
            metric_key = trigger_name[len("bear_") :]
            crossed = lambda actual: actual <= float(threshold)
            operator = "<="
        else:
            continue

        normalized_key = _normalized_metric_name(metric_key)
        match = next(
            (
                item
                for name, item in metric_by_name.items()
                if _normalized_metric_name(name) == normalized_key
            ),
            None,
        )
        if match is None or not isinstance(match.actual, (int, float)):
            continue
        if not crossed(float(match.actual)):
            continue

        detail = (
            f"deterministic_trigger:{match.metric} actual={float(match.actual):g} "
            f"{operator} {float(threshold):g}"
        )
        if isinstance(match.consensus, (int, float)) and match.percent_delta is not None:
            detail += f" consensus={float(match.consensus):g} delta={match.percent_delta:+.1f}%"
        hits.append((direction, detail))

    directions = {direction for direction, _ in hits}
    if not hits or len(directions) != 1:
        return None

    direction = hits[0][0]
    reasons = tuple(detail for _, detail in hits)
    return (
        ComponentAssessment("fundamental", direction, 20, 35, reasons),
        ComponentAssessment("catalyst", direction, 20, 25, reasons),
    )


def event_analysis_components(
    analysis: EventAnalysisPayload,
    *,
    expectation: EventExpectation | None = None,
    comparison: EventComparison | None = None,
) -> tuple[ComponentAssessment, ComponentAssessment]:
    """Combine deterministic event facts with bounded qualitative AI evidence."""
    ai_fundamental = ComponentAssessment(
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
    ai_catalyst = ComponentAssessment(
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

    if expectation is None or comparison is None:
        return ai_fundamental, ai_catalyst

    deterministic = deterministic_event_components(expectation, comparison)
    if deterministic is None:
        return ai_fundamental, ai_catalyst

    det_fundamental, det_catalyst = deterministic

    def merge(det: ComponentAssessment, ai: ComponentAssessment) -> ComponentAssessment:
        reasons = list(det.reasons)
        if ai.direction is det.direction:
            score = max(det.score, ai.score)
            reasons.extend(ai.reasons)
        elif ai.direction is Direction.NO_TRADE:
            score = det.score
            reasons.extend(ai.reasons)
        else:
            score = det.score
            reasons.append(
                f"ai_conflict:{ai.direction.value}:{ai.score}/{ai.max_score}; deterministic trigger retained"
            )
            reasons.extend(ai.reasons)
        return ComponentAssessment(det.name, det.direction, score, det.max_score, tuple(reasons))

    return merge(det_fundamental, ai_fundamental), merge(det_catalyst, ai_catalyst)


def build_event_strategy_inputs(
    *,
    instrument: str,
    event_id: str,
    analysis: EventAnalysisPayload,
    technical: ComponentAssessment,
    market_memory: ComponentAssessment,
    news_sentiment: ComponentAssessment,
    expectation: EventExpectation | None = None,
    comparison: EventComparison | None = None,
) -> StrategyInputs:
    fundamental, catalyst = event_analysis_components(
        analysis,
        expectation=expectation,
        comparison=comparison,
    )
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
