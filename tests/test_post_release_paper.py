import unittest

import pandas as pd

from tests.fixtures.hays_fy2026 import HAYS_FY2026
from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.models import ComponentAssessment, Direction, PortfolioState
from trading_system.post_release_paper import run_post_release_paper


class PostReleasePaperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = EventAnalysisPayload(
            metrics=[
                {
                    "name": "fy27_operating_profit_pre_exceptional_gbp_m",
                    "value": 61.0,
                    "unit": "GBP m",
                }
            ],
            guidance_summary="FY27 operating profit expected around £61m.",
            management_summary="Markets remain challenging but Germany is stabilising.",
            catalyst_direction="BULLISH",
            catalyst_score_0_25=18,
            fundamental_direction="BULLISH",
            fundamental_score_0_35=24,
            key_positive_surprises=["FY27 outlook above consensus"],
            key_negative_surprises=[],
            uncertainties=["recruitment recovery timing"],
            invalidation_flags=[],
            evidence_quotes=["FY27 operating profit around £61m"],
        )
        self.portfolio = PortfolioState(
            equity=10_000.0,
            cash=10_000.0,
            open_positions=0,
            spread_pct=0.3,
            volatility_pct=5.0,
        )
        self.technical = ComponentAssessment(
            "technical", Direction.LONG, 14, 20, ("aligned",)
        )
        self.memory = ComponentAssessment(
            "market_memory", Direction.LONG, 7, 10, ("aligned",)
        )

    def market_df(self, previous: float, current: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Close": [previous, current],
                "atr_pct": [4.0, 4.0],
            },
            index=pd.to_datetime(["2026-08-19", "2026-08-20"]),
        )

    def test_aligned_event_day_confirmation_reaches_paper_broker(self) -> None:
        result = run_post_release_paper(
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            portfolio=self.portfolio,
            market_df=self.market_df(0.80, 0.84),
            technical=self.technical,
            market_memory=self.memory,
        )

        self.assertEqual(result.status, "paper_executed")
        assert result.pipeline is not None
        self.assertEqual(result.pipeline.strategy.direction, Direction.LONG)
        self.assertIsNotNone(result.pipeline.order)
        self.assertEqual(result.pipeline.order.status, "FILLED_SIMULATED")

    def test_conflicting_price_reaction_stays_fail_closed(self) -> None:
        result = run_post_release_paper(
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            portfolio=self.portfolio,
            market_df=self.market_df(0.80, 0.76),
            technical=self.technical,
            market_memory=self.memory,
        )

        self.assertEqual(result.status, "waiting_confirmation")
        self.assertIn("price reaction conflicts", result.message)
        self.assertIsNone(result.pipeline)

    def test_pre_event_market_bar_cannot_trade(self) -> None:
        df = self.market_df(0.80, 0.84)
        df.index = pd.to_datetime(["2026-08-18", "2026-08-19"])
        result = run_post_release_paper(
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            portfolio=self.portfolio,
            market_df=df,
            technical=self.technical,
            market_memory=self.memory,
        )

        self.assertEqual(result.status, "waiting_confirmation")
        self.assertEqual(result.message, "no event-day market bar yet")
        assert result.completed_components is not None
        fundamental, catalyst = result.completed_components
        self.assertEqual((fundamental.score, catalyst.score), (24, 20))
        self.assertEqual(fundamental.direction, Direction.LONG)
        self.assertEqual(catalyst.direction, Direction.LONG)

    def test_later_bar_does_not_replace_event_day_reaction(self) -> None:
        df = pd.DataFrame(
            {
                "Close": [0.80, 0.84, 0.79],
                "atr_pct": [4.0, 4.0, 4.0],
            },
            index=pd.to_datetime(["2026-08-19", "2026-08-20", "2026-08-21"]),
        )
        result = run_post_release_paper(
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            portfolio=self.portfolio,
            market_df=df,
            technical=self.technical,
            market_memory=self.memory,
        )

        self.assertEqual(result.status, "paper_executed")

    def test_missing_spread_assumption_stays_fail_closed(self) -> None:
        portfolio = PortfolioState(
            equity=10_000.0,
            cash=10_000.0,
            open_positions=0,
            spread_pct=None,
            volatility_pct=None,
        )
        result = run_post_release_paper(
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            portfolio=portfolio,
            market_df=self.market_df(0.80, 0.84),
            technical=self.technical,
            market_memory=self.memory,
        )

        self.assertEqual(result.status, "waiting_confirmation")
        self.assertEqual(result.message, "paper spread assumption unavailable")


if __name__ == "__main__":
    unittest.main()
