import unittest
from datetime import UTC, datetime

from tests.fixtures.hays_fy2026 import HAYS_FY2026
from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.event_strategy_bridge import build_event_strategy_inputs
from trading_system.events import compare_event
from trading_system.models import ComponentAssessment, Direction, EventActuals
from trading_system.strategy import StrategyEngine


class EventStrategyBridgeTests(unittest.TestCase):
    def test_canonical_fy27_trigger_cannot_be_neutralized_by_ai(self) -> None:
        analysis = EventAnalysisPayload.model_validate(
            {
                "metrics": [
                    {
                        "name": "fy27_operating_profit_pre_exceptional_gbp_m",
                        "value": 61.0,
                        "unit": "GBP m",
                    }
                ],
                "guidance_summary": "FY27 operating profit guidance is £61m.",
                "management_summary": "Recruitment markets remain challenging.",
                "catalyst_direction": "BULLISH",
                "catalyst_score_0_25": 22,
                "fundamental_direction": "NEUTRAL",
                "fundamental_score_0_35": 18,
                "key_positive_surprises": [],
                "key_negative_surprises": [],
                "uncertainties": ["Recruitment markets remain challenging."],
                "invalidation_flags": [],
                "evidence_quotes": [],
            }
        )
        actuals = EventActuals(
            event_id=HAYS_FY2026.event_id,
            instrument=HAYS_FY2026.instrument,
            published_at=datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
            source_url="https://example.invalid/hays-results",
            metrics=analysis.metric_values(),
            guidance_summary=analysis.guidance_summary,
        )
        comparison = compare_event(HAYS_FY2026, actuals)

        technical = ComponentAssessment("technical", Direction.LONG, 14, 20)
        memory = ComponentAssessment("market_memory", Direction.LONG, 7, 10)
        news = ComponentAssessment("news_sentiment", Direction.NO_TRADE, 0, 10)

        inputs = build_event_strategy_inputs(
            instrument=HAYS_FY2026.instrument,
            event_id=HAYS_FY2026.event_id,
            analysis=analysis,
            technical=technical,
            market_memory=memory,
            news_sentiment=news,
            expectation=HAYS_FY2026,
            comparison=comparison,
        )
        decision = StrategyEngine().evaluate(inputs)

        self.assertEqual(inputs.fundamental.direction, Direction.LONG)
        self.assertEqual(inputs.fundamental.score, 20)
        self.assertEqual(inputs.catalyst.direction, Direction.LONG)
        self.assertEqual(inputs.catalyst.score, 22)
        self.assertEqual(decision.direction, Direction.LONG)
        self.assertEqual(decision.confidence, 63)
        self.assertTrue(any("deterministic_trigger" in reason for reason in inputs.fundamental.reasons))

    def test_without_trigger_crossing_ai_direction_is_unchanged(self) -> None:
        analysis = EventAnalysisPayload.model_validate(
            {
                "metrics": [
                    {
                        "name": "fy27_operating_profit_pre_exceptional_gbp_m",
                        "value": 55.6,
                        "unit": "GBP m",
                    }
                ],
                "guidance_summary": "FY27 outlook is in line with consensus.",
                "management_summary": "No material change.",
                "catalyst_direction": "NEUTRAL",
                "catalyst_score_0_25": 10,
                "fundamental_direction": "NEUTRAL",
                "fundamental_score_0_35": 12,
                "key_positive_surprises": [],
                "key_negative_surprises": [],
                "uncertainties": [],
                "invalidation_flags": [],
                "evidence_quotes": [],
            }
        )
        actuals = EventActuals(
            event_id=HAYS_FY2026.event_id,
            instrument=HAYS_FY2026.instrument,
            published_at=datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
            source_url="https://example.invalid/hays-results",
            metrics=analysis.metric_values(),
        )
        comparison = compare_event(HAYS_FY2026, actuals)

        inputs = build_event_strategy_inputs(
            instrument=HAYS_FY2026.instrument,
            event_id=HAYS_FY2026.event_id,
            analysis=analysis,
            technical=ComponentAssessment("technical", Direction.NO_TRADE, 0, 20),
            market_memory=ComponentAssessment("market_memory", Direction.NO_TRADE, 0, 10),
            news_sentiment=ComponentAssessment("news_sentiment", Direction.NO_TRADE, 0, 10),
            expectation=HAYS_FY2026,
            comparison=comparison,
        )

        self.assertEqual(inputs.fundamental.direction, Direction.NO_TRADE)
        self.assertEqual(inputs.fundamental.score, 12)
        self.assertEqual(inputs.catalyst.direction, Direction.NO_TRADE)
        self.assertEqual(inputs.catalyst.score, 10)


if __name__ == "__main__":
    unittest.main()
