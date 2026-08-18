import json
import os
import unittest
from datetime import date
from unittest.mock import patch

from trading_system.ai_event_analyzer import (
    AIEventAnalysis,
    EventAnalysisPayload,
    FallbackEventAnalyzer,
    GroqEventAnalyzer,
    _parse_payload,
    _select_release_text,
)
from trading_system.models import EventExpectation
from trading_system.release_ingestion import ReleaseDocument


PAYLOAD = {
    "metrics": [
        {
            "name": "fy27_operating_profit_pre_exceptional_gbp_m",
            "value": 61.0,
            "unit": "GBP million",
        }
    ],
    "guidance_summary": "FY27 outlook above consensus.",
    "management_summary": "Management remains cautious.",
    "catalyst_direction": "BULLISH",
    "catalyst_score_0_25": 20,
    "fundamental_direction": "BULLISH",
    "fundamental_score_0_35": 22,
    "key_positive_surprises": ["FY27 outlook stronger"],
    "key_negative_surprises": [],
    "uncertainties": ["market recovery timing"],
    "invalidation_flags": [],
    "evidence_quotes": ["outlook remains challenging"],
}


def expectation() -> EventExpectation:
    return EventExpectation(
        event_id="hays-fy2026-results",
        instrument="HAS.L",
        event_name="Hays plc FY2026 results",
        scheduled_date=date(2026, 8, 20),
        consensus={"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
        important_kpis=("guidance", "germany_net_fee_trend"),
    )


def document() -> ReleaseDocument:
    return ReleaseDocument(
        event_id="hays-fy2026-results",
        source_type="official_results_centre",
        source_url="https://example.test/hays-results",
        source_title="Hays FY26 results",
        raw_text="Official release text.",
    )


class StubAnalyzer:
    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def analyze(self, expectation, document):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class AIProviderTests(unittest.TestCase):
    def test_groq_schema_limits_metric_names_to_event_keys(self) -> None:
        analyzer = GroqEventAnalyzer(api_key="test", model="openai/gpt-oss-120b")
        response = {"choices": [{"message": {"content": json.dumps(PAYLOAD)}}]}

        with patch("trading_system.ai_event_analyzer._post_json", return_value=response) as post:
            result = analyzer.analyze(expectation(), document())

        self.assertEqual(result.provider, "groq")
        self.assertEqual(
            result.payload.metric_values()["fy27_operating_profit_pre_exceptional_gbp_m"],
            61.0,
        )
        schema = post.call_args.args[1]["response_format"]["json_schema"]["schema"]
        allowed = schema["$defs"]["ExtractedMetric"]["properties"]["name"]["enum"]
        self.assertEqual(
            allowed,
            [
                "fy27_operating_profit_pre_exceptional_gbp_m",
                "guidance",
                "germany_net_fee_trend",
            ],
        )

    def test_non_canonical_metric_name_is_rejected_after_provider_response(self) -> None:
        bad = {**PAYLOAD, "metrics": [{"name": "FY27 operating profit", "value": 61.0, "unit": "GBP million"}]}
        with self.assertRaisesRegex(RuntimeError, "non-canonical metric names"):
            _parse_payload(json.dumps(bad), expectation())

    def test_release_excerpt_respects_budget_and_keeps_late_guidance(self) -> None:
        filler = "General corporate information without a catalyst. " * 500
        late_guidance = (
            "FY27 guidance and outlook: operating profit expectations improve, "
            "with Germany net fees stabilising and structural cost savings continuing."
        )
        raw = filler + "\n\n" + late_guidance
        with patch.dict(os.environ, {"MARKETAI_AI_RELEASE_CHAR_BUDGET": "5000"}):
            excerpt = _select_release_text(expectation(), raw)
        self.assertLessEqual(len(excerpt), 5000)
        self.assertIn("FY27 guidance and outlook", excerpt)

    def test_fallback_uses_second_analyzer_after_first_failure(self) -> None:
        first = StubAnalyzer(error=RuntimeError("rate limited"))
        expected = AIEventAnalysis(
            payload=EventAnalysisPayload.model_validate(PAYLOAD),
            provider="ollama",
            model="gpt-oss:20b",
            raw_response=json.dumps(PAYLOAD),
        )
        second = StubAnalyzer(result=expected)
        result = FallbackEventAnalyzer([first, second]).analyze(expectation(), document())
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    def test_fallback_reports_all_provider_failures(self) -> None:
        analyzer = FallbackEventAnalyzer(
            [
                StubAnalyzer(error=RuntimeError("groq down")),
                StubAnalyzer(error=RuntimeError("ollama down")),
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "All event analyzers failed"):
            analyzer.analyze(expectation(), document())


if __name__ == "__main__":
    unittest.main()
