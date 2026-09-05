from __future__ import annotations

import unittest
from typing import Any

from tests.fixtures.hays_fy2026 import HAYS_FY2026
from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.release_worker import _terminal_paper_status, run_paper_confirmation_loop


class _Persistence:
    def __init__(self, status: str) -> None:
        self.status = status

    def get_latest_for_event(self, event_id: str) -> dict[str, Any]:
        return {"status": self.status, "analysis_id": "analysis-1"}


class ReleaseWorkerCanonicalTerminalGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = EventAnalysisPayload(
            metrics=[],
            guidance_summary="",
            management_summary="",
            catalyst_direction="NEUTRAL",
            catalyst_score_0_25=0,
            fundamental_direction="NEUTRAL",
            fundamental_score_0_35=0,
            key_positive_surprises=[],
            key_negative_surprises=[],
            uncertainties=[],
            invalidation_flags=[],
            evidence_quotes=[],
        )

    def test_terminal_paper_status_uses_canonical_terminal_states(self) -> None:
        self.assertEqual(
            _terminal_paper_status(_Persistence("paper_executed"), "event-1"),
            "paper_executed",
        )
        self.assertEqual(
            _terminal_paper_status(_Persistence("expired_no_trade"), "event-1"),
            "expired_no_trade",
        )
        self.assertIsNone(
            _terminal_paper_status(_Persistence("waiting_confirmation"), "event-1")
        )

    def test_terminal_states_block_runner_across_retries_and_restarts(self) -> None:
        for terminal_status in ("paper_executed", "expired_no_trade"):
            with self.subTest(terminal_status=terminal_status):
                runner_calls = 0

                def runner(**_: Any):
                    nonlocal runner_calls
                    runner_calls += 1
                    raise AssertionError(
                        "runner must not execute after a terminal PAPER lifecycle state"
                    )

                persistence = _Persistence(terminal_status)
                first = run_paper_confirmation_loop(
                    event_id="hays-fy2026-results",
                    expectation=HAYS_FY2026,
                    analysis=self.analysis,
                    interval_seconds=300,
                    once=True,
                    analysis_id="analysis-1",
                    persistence=persistence,
                    runner=runner,
                )
                second = run_paper_confirmation_loop(
                    event_id="hays-fy2026-results",
                    expectation=HAYS_FY2026,
                    analysis=self.analysis,
                    interval_seconds=300,
                    once=True,
                    analysis_id="analysis-1",
                    persistence=persistence,
                    runner=runner,
                )

                self.assertEqual(first.status, terminal_status)
                self.assertEqual(second.status, terminal_status)
                self.assertEqual(runner_calls, 0)

    def test_unknown_persisted_status_fails_closed_before_runner(self) -> None:
        runner_called = False

        def runner(**_: Any):
            nonlocal runner_called
            runner_called = True
            raise AssertionError("runner must not execute for unknown persisted lifecycle state")

        with self.assertRaises(ValueError):
            run_paper_confirmation_loop(
                event_id="hays-fy2026-results",
                expectation=HAYS_FY2026,
                analysis=self.analysis,
                interval_seconds=300,
                once=True,
                analysis_id="analysis-1",
                persistence=_Persistence("corrupted_status"),
                runner=runner,
            )

        self.assertFalse(runner_called)

    def test_padded_persisted_status_fails_closed_before_runner(self) -> None:
        runner_called = False

        def runner(**_: Any):
            nonlocal runner_called
            runner_called = True
            raise AssertionError("runner must not execute for padded lifecycle state")

        with self.assertRaisesRegex(ValueError, "surrounding whitespace"):
            run_paper_confirmation_loop(
                event_id="hays-fy2026-results",
                expectation=HAYS_FY2026,
                analysis=self.analysis,
                interval_seconds=300,
                once=True,
                analysis_id="analysis-1",
                persistence=_Persistence(" waiting_confirmation "),
                runner=runner,
            )

        self.assertFalse(runner_called)


if __name__ == "__main__":
    unittest.main()
