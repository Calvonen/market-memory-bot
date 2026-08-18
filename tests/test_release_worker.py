import unittest
from unittest.mock import patch

from tests.fixtures.hays_fy2026 import HAYS_FY2026
from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.post_release_paper import PostReleasePaperResult
from trading_system.release_worker import build_paper_portfolio_from_env, run_paper_confirmation_loop


class ReleaseWorkerPaperConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = EventAnalysisPayload(
            metrics=[
                {
                    "name": "fy27_operating_profit_pre_exceptional_gbp_m",
                    "value": 61.0,
                    "unit": "GBP m",
                }
            ],
            guidance_summary="FY27 outlook above consensus",
            management_summary="management summary",
            catalyst_direction="BULLISH",
            catalyst_score_0_25=18,
            fundamental_direction="BULLISH",
            fundamental_score_0_35=24,
            key_positive_surprises=[],
            key_negative_surprises=[],
            uncertainties=[],
            invalidation_flags=[],
            evidence_quotes=[],
        )

    def test_confirmation_loop_waits_without_reanalyzing_then_stops_after_paper_execution(self) -> None:
        results = iter(
            [
                PostReleasePaperResult("waiting_confirmation", "no event-day market bar yet"),
                PostReleasePaperResult("paper_executed", "LONG 100 HAS.L FILLED_SIMULATED"),
            ]
        )
        runner_calls = []
        sleeps = []

        def runner(**kwargs):
            runner_calls.append(kwargs)
            return next(results)

        result = run_paper_confirmation_loop(
            event_id="hays-fy2026-results",
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            interval_seconds=300,
            once=False,
            runner=runner,
            sleeper=sleeps.append,
        )

        self.assertEqual(result.status, "paper_executed")
        self.assertEqual(len(runner_calls), 2)
        self.assertEqual(sleeps, [300])
        self.assertIs(runner_calls[0]["analysis"], self.analysis)

    def test_once_mode_runs_only_one_confirmation_attempt(self) -> None:
        calls = []

        def runner(**kwargs):
            calls.append(kwargs)
            return PostReleasePaperResult("waiting_confirmation", "technical confirmation is not aligned")

        result = run_paper_confirmation_loop(
            event_id="hays-fy2026-results",
            expectation=HAYS_FY2026,
            analysis=self.analysis,
            interval_seconds=300,
            once=True,
            runner=runner,
            sleeper=lambda _: self.fail("once mode must not sleep"),
        )

        self.assertEqual(result.status, "waiting_confirmation")
        self.assertEqual(len(calls), 1)

    def test_paper_portfolio_has_explicit_spread_assumption_and_live_mode_is_not_involved(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MARKETAI_PAPER_EQUITY": "12000",
                "MARKETAI_PAPER_CASH": "11000",
                "MARKETAI_PAPER_SPREAD_PCT": "0.45",
            },
            clear=False,
        ):
            portfolio = build_paper_portfolio_from_env()

        self.assertEqual(portfolio.equity, 12000.0)
        self.assertEqual(portfolio.cash, 11000.0)
        self.assertEqual(portfolio.spread_pct, 0.45)
        self.assertIsNone(portfolio.volatility_pct)


if __name__ == "__main__":
    unittest.main()
