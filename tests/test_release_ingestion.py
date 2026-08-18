import unittest

from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.event_strategy_bridge import event_analysis_components
from trading_system.models import Direction
from trading_system.release_ingestion import HaysResultsCentreProvider


class FakeHaysProvider(HaysResultsCentreProvider):
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def _fetch(self, url: str) -> str:
        return self.pages[url]


class ReleaseIngestionTests(unittest.TestCase):
    def test_no_release_yet_returns_none(self) -> None:
        provider = FakeHaysProvider(
            {provider_url(): '<html><a href="/q4">Quarterly update for the three months ended 30 June 2026</a></html>'}
        )
        self.assertIsNone(provider.discover("hays-fy2026-results"))

    def test_official_full_year_link_is_discovered(self) -> None:
        release_url = "https://www.haysplc.com/results/fy26"
        body = " ".join(["Official FY26 results text"] * 80)
        provider = FakeHaysProvider(
            {
                provider_url(): '<a href="/results/fy26">Full-year results for the year ended 30 June 2026</a>',
                release_url: f"<html><body>{body}</body></html>",
            }
        )
        document = provider.discover("hays-fy2026-results")
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.source_url, release_url)
        self.assertEqual(len(document.content_sha256), 64)

    def test_ai_scores_are_bounded_to_strategy_component_budgets(self) -> None:
        analysis = EventAnalysisPayload(
            metrics=[
                {
                    "name": "fy27_operating_profit_pre_exceptional_gbp_m",
                    "value": 61.0,
                    "unit": "GBP million",
                }
            ],
            guidance_summary="FY27 outlook above consensus",
            management_summary="Cost actions progressing",
            catalyst_direction="BULLISH",
            catalyst_score_0_25=24,
            fundamental_direction="BULLISH",
            fundamental_score_0_35=31,
            key_positive_surprises=["FY27 outlook stronger"],
            key_negative_surprises=[],
            uncertainties=["market recovery timing"],
            invalidation_flags=[],
            evidence_quotes=["outlook improved"],
        )
        fundamental, catalyst = event_analysis_components(analysis)
        self.assertEqual(fundamental.direction, Direction.LONG)
        self.assertEqual(fundamental.max_score, 35)
        self.assertEqual(catalyst.max_score, 25)
        self.assertEqual(catalyst.score, 24)


def provider_url() -> str:
    return HaysResultsCentreProvider.RESULTS_URL


if __name__ == "__main__":
    unittest.main()
