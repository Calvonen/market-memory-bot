from __future__ import annotations

from dataclasses import dataclass

from trading_system.models import (
    ComponentAssessment,
    Direction,
    ScoreBreakdown,
    StrategyDecision,
    StrategyInputs,
)


EXPECTED_MAX_SCORES = {
    "fundamental": 35,
    "catalyst": 25,
    "technical": 20,
    "market_memory": 10,
    "news_sentiment": 10,
}


@dataclass(frozen=True)
class StrategyConfig:
    min_confidence: int = 60
    min_direction_margin: int = 15


class StrategyEngine:
    """Combine structured evidence; never execute trades or size positions."""

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()

    def evaluate(self, inputs: StrategyInputs) -> StrategyDecision:
        components = {
            "fundamental": inputs.fundamental,
            "catalyst": inputs.catalyst,
            "technical": inputs.technical,
            "market_memory": inputs.market_memory,
            "news_sentiment": inputs.news_sentiment,
        }
        self._validate_components(components)

        long_evidence = sum(c.score for c in components.values() if c.direction is Direction.LONG)
        short_evidence = sum(c.score for c in components.values() if c.direction is Direction.SHORT)
        margin = abs(long_evidence - short_evidence)

        if long_evidence > short_evidence:
            proposed_direction = Direction.LONG
            confidence = long_evidence
        elif short_evidence > long_evidence:
            proposed_direction = Direction.SHORT
            confidence = short_evidence
        else:
            proposed_direction = Direction.NO_TRADE
            confidence = max(long_evidence, short_evidence)

        if confidence < self.config.min_confidence or margin < self.config.min_direction_margin:
            direction = Direction.NO_TRADE
        else:
            direction = proposed_direction

        chosen_scores = ScoreBreakdown(
            fundamental=self._score_for_direction(inputs.fundamental, direction),
            catalyst=self._score_for_direction(inputs.catalyst, direction),
            technical=self._score_for_direction(inputs.technical, direction),
            market_memory=self._score_for_direction(inputs.market_memory, direction),
            news_sentiment=self._score_for_direction(inputs.news_sentiment, direction),
        )

        rationale: list[str] = []
        for component in components.values():
            prefix = f"{component.name}:{component.direction.value}:{component.score}/{component.max_score}"
            if component.reasons:
                rationale.extend(f"{prefix} - {reason}" for reason in component.reasons)
            else:
                rationale.append(prefix)

        if direction is Direction.NO_TRADE:
            rationale.append(
                f"strategy_gate:NO_TRADE confidence={confidence} margin={margin} "
                f"min_confidence={self.config.min_confidence} min_margin={self.config.min_direction_margin}"
            )

        return StrategyDecision(
            instrument=inputs.instrument,
            direction=direction,
            confidence=confidence,
            scores=chosen_scores,
            rationale=tuple(rationale),
            invalidation=inputs.invalidation,
            source_event_id=inputs.source_event_id,
            long_evidence=long_evidence,
            short_evidence=short_evidence,
        )

    @staticmethod
    def _score_for_direction(component: ComponentAssessment, direction: Direction) -> int:
        if direction is Direction.NO_TRADE:
            return 0
        return component.score if component.direction is direction else 0

    @staticmethod
    def _validate_components(components: dict[str, ComponentAssessment]) -> None:
        for key, component in components.items():
            expected_max = EXPECTED_MAX_SCORES[key]
            if component.name != key:
                raise ValueError(f"Component name mismatch: expected {key}, got {component.name}")
            if component.max_score != expected_max:
                raise ValueError(f"{key} max_score must be {expected_max}")
            if component.score < 0 or component.score > component.max_score:
                raise ValueError(f"{key} score outside allowed range")
