from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from trading_system.models import EventExpectation, PortfolioState, TradingMode
from trading_system.post_release_paper import PostReleasePaperResult
from trading_system.tracked_event_paper_bridge import CanonicalTradingTaskExecutionContext
from trading_system.tracked_event_paper_orchestration import run_approved_tracked_paper_once
from trading_system.tracked_event_repository import TrackedEventStatus, TrackedEventTimeStatus
from trading_system.trading_task import TradingTaskState


TRACKED_ID = "22222222-2222-2222-2222-222222222222"
TASK_ID = "11111111-1111-1111-1111-111111111111"
ANALYSIS_ID = "33333333-3333-3333-3333-333333333333"
DOCUMENT_ID = "44444444-4444-4444-4444-444444444444"
RELEASE_EVENT_ID = f"tracked:{TRACKED_ID}"
_DEFAULT_ANALYSIS = object()


def event():
    return SimpleNamespace(
        event_id=TRACKED_ID,
        tracked_instrument_id="tracked-instrument-1",
        calendar_event_id=None,
        company_name="Example Ltd",
        instrument="EXM.ASX",
        market="ASX",
        source="manual",
        external_key="example-results",
        kind="earnings",
        title="FY26 results",
        event_at=datetime(2026, 8, 31, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.MONITORING,
    )


def expectation():
    return EventExpectation(
        event_id=RELEASE_EVENT_ID,
        instrument="EXM.ASX",
        event_name="FY26 results",
        scheduled_date=date(2026, 8, 31),
        version=3,
    )


def analysis_row():
    return {
        "id": ANALYSIS_ID,
        "event_id": RELEASE_EVENT_ID,
        "source_document_id": DOCUMENT_ID,
        "expectation_version": 3,
        "provider": "groq",
        "model": "test-model",
        "raw_response": "{}",
        "analysis": {
            "metrics": [],
            "guidance_summary": "guidance",
            "management_summary": "management",
            "catalyst_direction": "BULLISH",
            "catalyst_score_0_25": 20,
            "fundamental_direction": "BULLISH",
            "fundamental_score_0_35": 28,
            "key_positive_surprises": [],
            "key_negative_surprises": [],
            "uncertainties": [],
            "invalidation_flags": [],
            "evidence_quotes": [],
        },
    }


def task(*, state=TradingTaskState.APPROVED, mode=TradingMode.PAPER, tracked_event_id=TRACKED_ID):
    return SimpleNamespace(
        task_id=TASK_ID,
        tracked_event_id=tracked_event_id,
        source_event_id=RELEASE_EVENT_ID,
        instrument="EXM.ASX",
        mode=mode,
        state=state,
    )


class TrackedEvents:
    def __init__(self) -> None:
        self.reaction_reads = 0

    def get(self, event_id: str):
        return event() if event_id == TRACKED_ID else None

    def list_reactions(self, event_id: str):
        self.reaction_reads += 1
        self.last_reaction_event_id = event_id
        return ("persisted-reaction",)


class Expectations:
    def get(self, event_id: str):
        return expectation() if event_id == RELEASE_EVENT_ID else None


class Releases:
    def __init__(self, row=_DEFAULT_ANALYSIS) -> None:
        self.row = analysis_row() if row is _DEFAULT_ANALYSIS else row

    def get_analysis_for_event_version(self, *, event_id: str, expectation_version: int):
        self.read = (event_id, expectation_version)
        return self.row


class TradingTasks:
    def __init__(self, value) -> None:
        self.value = value
        self.execution_context_calls = 0

    def get(self, task_id: str):
        self.requested = task_id
        return self.value

    def execution_context(self, task_id: str):
        self.execution_context_calls += 1
        return CanonicalTradingTaskExecutionContext(
            task_id=task_id,
            source_event_id=RELEASE_EVENT_ID,
            instrument="EXM.ASX",
            mode=TradingMode.PAPER,
        )


class PaperRuns:
    def __init__(self, *, claim=None) -> None:
        self.claim_token = "claim-token"
        self.claim = claim or {
            "event_id": RELEASE_EVENT_ID,
            "analysis_id": ANALYSIS_ID,
            "claim_token": self.claim_token,
            "lease_expires_at": "2026-08-31T00:05:00+00:00",
        }
        self.claim_calls = []
        self.save_calls = []

    def claim_event(self, **kwargs):
        self.claim_calls.append(kwargs)
        return self.claim

    def save_result(self, **kwargs):
        self.save_calls.append(kwargs)
        return {"status": kwargs["result"].status, "analysis_id": kwargs["analysis_id"]}


PORTFOLIO = PortfolioState(
    equity=10000,
    cash=10000,
    open_positions=0,
    spread_pct=0.1,
    volatility_pct=2.0,
)


class TrackedEventPaperOrchestrationTests(unittest.TestCase):
    def test_pending_task_never_claims_or_calls_pipeline(self) -> None:
        paper = PaperRuns()
        tasks = TradingTasks(task(state=TradingTaskState.PENDING))
        with patch(
            "trading_system.tracked_event_paper_orchestration.run_post_release_paper_from_tracked_event"
        ) as bridge:
            result = run_approved_tracked_paper_once(
                tracked_event_id=TRACKED_ID,
                task_id=TASK_ID,
                tracked_events=TrackedEvents(),
                expectations=Expectations(),
                releases=Releases(),
                trading_tasks=tasks,
                paper_runs=paper,
                resolver=SimpleNamespace(),
                portfolio=PORTFOLIO,
            )
        self.assertEqual(result.status, "waiting_approval")
        self.assertEqual(paper.claim_calls, [])
        bridge.assert_not_called()

    def test_task_for_other_tracked_event_fails_before_claim(self) -> None:
        paper = PaperRuns()
        tasks = TradingTasks(task(tracked_event_id="different-event"))
        with self.assertRaisesRegex(RuntimeError, "different tracked event"):
            run_approved_tracked_paper_once(
                tracked_event_id=TRACKED_ID,
                task_id=TASK_ID,
                tracked_events=TrackedEvents(),
                expectations=Expectations(),
                releases=Releases(),
                trading_tasks=tasks,
                paper_runs=paper,
                resolver=SimpleNamespace(),
                portfolio=PORTFOLIO,
            )
        self.assertEqual(paper.claim_calls, [])

    def test_missing_current_analysis_waits_before_task_or_claim(self) -> None:
        paper = PaperRuns()
        tasks = TradingTasks(task())
        result = run_approved_tracked_paper_once(
            tracked_event_id=TRACKED_ID,
            task_id=TASK_ID,
            tracked_events=TrackedEvents(),
            expectations=Expectations(),
            releases=Releases(row=None),
            trading_tasks=tasks,
            paper_runs=paper,
            resolver=SimpleNamespace(),
            portfolio=PORTFOLIO,
        )
        self.assertEqual(result.status, "waiting_analysis")
        self.assertEqual(paper.claim_calls, [])
        self.assertFalse(hasattr(tasks, "requested"))

    def test_unowned_claim_never_calls_bridge(self) -> None:
        paper = PaperRuns(
            claim={
                "event_id": RELEASE_EVENT_ID,
                "analysis_id": "other-analysis",
                "claim_token": "other-token",
            }
        )
        with patch(
            "trading_system.tracked_event_paper_orchestration.run_post_release_paper_from_tracked_event"
        ) as bridge:
            result = run_approved_tracked_paper_once(
                tracked_event_id=TRACKED_ID,
                task_id=TASK_ID,
                tracked_events=TrackedEvents(),
                expectations=Expectations(),
                releases=Releases(),
                trading_tasks=TradingTasks(task()),
                paper_runs=paper,
                resolver=SimpleNamespace(),
                portfolio=PORTFOLIO,
            )
        self.assertEqual(result.status, "claim_not_owned")
        bridge.assert_not_called()
        self.assertEqual(paper.save_calls, [])

    def test_terminal_claim_never_reexecutes(self) -> None:
        paper = PaperRuns(
            claim={
                "event_id": RELEASE_EVENT_ID,
                "analysis_id": ANALYSIS_ID,
                "claim_token": "old-token",
                "terminal_status": "paper_executed",
            }
        )
        with patch(
            "trading_system.tracked_event_paper_orchestration.run_post_release_paper_from_tracked_event"
        ) as bridge:
            result = run_approved_tracked_paper_once(
                tracked_event_id=TRACKED_ID,
                task_id=TASK_ID,
                tracked_events=TrackedEvents(),
                expectations=Expectations(),
                releases=Releases(),
                trading_tasks=TradingTasks(task()),
                paper_runs=paper,
                resolver=SimpleNamespace(),
                portfolio=PORTFOLIO,
            )
        self.assertEqual(result.status, "paper_executed")
        bridge.assert_not_called()
        self.assertEqual(paper.save_calls, [])

    def test_approved_owned_claim_runs_bridge_once_and_persists_exact_identity(self) -> None:
        tracked = TrackedEvents()
        paper = PaperRuns()
        tasks = TradingTasks(task())
        bridge_result = PostReleasePaperResult("waiting_confirmation", "technical pending")
        with patch(
            "trading_system.tracked_event_paper_orchestration.run_post_release_paper_from_tracked_event",
            return_value=bridge_result,
        ) as bridge:
            result = run_approved_tracked_paper_once(
                tracked_event_id=TRACKED_ID,
                task_id=TASK_ID,
                tracked_events=tracked,
                expectations=Expectations(),
                releases=Releases(),
                trading_tasks=tasks,
                paper_runs=paper,
                resolver=SimpleNamespace(),
                portfolio=PORTFOLIO,
            )

        self.assertEqual(result.status, "waiting_confirmation")
        self.assertEqual(tasks.execution_context_calls, 1)
        self.assertEqual(tracked.reaction_reads, 1)
        self.assertEqual(len(paper.claim_calls), 1)
        bridge.assert_called_once()
        self.assertEqual(len(paper.save_calls), 1)
        saved = paper.save_calls[0]
        self.assertEqual(saved["event_id"], RELEASE_EVENT_ID)
        self.assertEqual(saved["expectation_version"], 3)
        self.assertEqual(saved["source_document_id"], DOCUMENT_ID)
        self.assertEqual(saved["analysis_id"], ANALYSIS_ID)
        self.assertEqual(saved["claim_token"], paper.claim_token)
        self.assertIs(saved["result"], bridge_result)


if __name__ == "__main__":
    unittest.main()
