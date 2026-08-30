from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.models import (
    ComponentAssessment,
    Direction,
    EventExpectation,
    PortfolioState,
)
from trading_system.post_release_paper import run_post_release_paper


class PostReleaseTrackedReactionOverrideTests(unittest.TestCase):
    def test_confirmed_reaction_does_not_require_event_day_bar(self) -> None:
        market_df = pd.DataFrame(
            {
                "Close": [100.0, 101.0],
                "atr_pct": [2.0, 2.0],
            },
            index=pd.to_datetime(["2026-08-28", "2026-08-29"]),
        )
        expectation = EventExpectation(
            event_id="tracked:event-1",
            instrument="EXM.ASX",
            event_name="FY26 results",
            scheduled_date=date(2026, 8, 31),
        )
        analysis = EventAnalysisPayload(
            metrics=[],
            guidance_summary="guidance",
            management_summary="management",
            catalyst_direction="BULLISH",
            catalyst_score_0_25=20,
            fundamental_direction="BULLISH",
            fundamental_score_0_35=30,
            key_positive_surprises=[],
            key_negative_surprises=[],
            uncertainties=[],
            invalidation_flags=[],
            evidence_quotes=[],
        )
        long = ComponentAssessment("technical", Direction.LONG, 10, 20, ())
        memory = ComponentAssessment("market_memory", Direction.LONG, 10, 10, ())

        result = run_post_release_paper(
            expectation=expectation,
            analysis=analysis,
            portfolio=PortfolioState(
                equity=10_000,
                cash=10_000,
                open_positions=0,
                spread_pct=0.1,
                volatility_pct=2.0,
            ),
            market_df=market_df,
            technical=long,
            market_memory=memory,
            confirmed_reaction_pct=2.5,
        )

        self.assertNotEqual(result.message, "no event-day market bar yet")


if __name__ == "__main__":
    unittest.main()
