from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_system.event_workflow_readiness import WorkflowExecutionOutcome
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)
from trading_system.workflow_readiness_evidence_loader import (
    SupabaseWorkflowReadinessEvidenceLoader,
    canonical_release_event_id,
)


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self.desc = False
        self.limit_count = None

    def select(self, _fields):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, field, desc=False):
        self.order_field = field
        self.desc = desc
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def execute(self):
        rows = [
            row
            for row in self.rows
            if all(row.get(field) == value for field, value in self.filters)
        ]
        if hasattr(self, "order_field"):
            rows.sort(key=lambda row: row.get(self.order_field) or "", reverse=self.desc)
        if self.limit_count is not None:
            rows = rows[: self.limit_count]
        return _Response(rows)


class _RpcQuery:
    def __init__(self, rows):
        self.rows = rows

    def execute(self):
        return _Response(self.rows)


class _Client:
    def __init__(self, *, tables=None, paper_state=None):
        self.tables = tables or {}
        self.paper_state = paper_state
        self.rpc_calls = []

    def table(self, name):
        return _Query(self.tables.get(name, []))

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if name != "get_event_paper_trade_state":
            raise AssertionError(name)
        return _RpcQuery([self.paper_state] if self.paper_state is not None else [])


def _event(*, calendar_event_id=None, status=TrackedEventStatus.MONITORING):
    return PersistentTrackedEvent(
        event_id="tracked-123",
        tracked_instrument_id="instrument-1",
        calendar_event_id=calendar_event_id,
        company_name="Example Plc",
        instrument="EXM",
        market="USA",
        source="manual",
        external_key="example",
        kind="earnings",
        title="Example earnings",
        event_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=status,
    )


def _current_expectation(release_id: str, version: int = 2):
    return {"event_id": release_id, "version": version}


class WorkflowReadinessEvidenceLoaderTests(unittest.TestCase):
    def test_release_identity_is_calendar_bound_when_calendar_id_exists(self):
        self.assertEqual(
            canonical_release_event_id(_event(calendar_event_id="calendar-9")),
            "calendar:calendar-9",
        )

    def test_release_identity_is_tracked_for_calendarless_event(self):
        self.assertEqual(canonical_release_event_id(_event()), "tracked:tracked-123")

    def test_loads_release_analysis_reaction_and_filled_paper_evidence(self):
        release_id = "tracked:tracked-123"
        client = _Client(
            tables={
                "current_event_expectations": [_current_expectation(release_id)],
                "event_source_documents": [{"id": "doc-1", "event_id": release_id}],
                "event_ai_analyses": [
                    {
                        "id": "analysis-1",
                        "event_id": release_id,
                        "expectation_version": 2,
                    }
                ],
                "tracked_market_event_reactions": [
                    {"id": "reaction-1", "tracked_market_event_id": "tracked-123"}
                ],
                "event_ingestion_runs": [
                    {
                        "event_id": release_id,
                        "status": "analyzed",
                        "error_message": None,
                        "created_at": "2026-08-28T06:00:00+00:00",
                    }
                ],
            },
            paper_state={
                "status": "paper_executed",
                "expectation_version": 2,
                "strategy": {"decision_id": "s1"},
                "risk": {"status": "approved"},
                "paper_order": {"status": "FILLED_SIMULATED"},
            },
        )
        evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(_event())
        self.assertTrue(evidence.release_document_present)
        self.assertFalse(evidence.release_failed)
        self.assertTrue(evidence.analysis_present)
        self.assertTrue(evidence.reaction_present)
        self.assertTrue(evidence.strategy_present)
        self.assertTrue(evidence.risk_present)
        self.assertEqual(evidence.execution_outcome, WorkflowExecutionOutcome.FILLED)
        self.assertEqual(
            client.rpc_calls,
            [("get_event_paper_trade_state", {"input_event_id": release_id})],
        )

    def test_stale_analysis_and_paper_state_do_not_complete_current_workflow(self):
        release_id = "tracked:tracked-123"
        client = _Client(
            tables={
                "current_event_expectations": [_current_expectation(release_id, version=3)],
                "event_ai_analyses": [
                    {
                        "id": "analysis-old",
                        "event_id": release_id,
                        "expectation_version": 2,
                    }
                ],
            },
            paper_state={
                "status": "paper_executed",
                "expectation_version": 2,
                "strategy": {"decision_id": "old"},
                "risk": {"status": "approved"},
                "paper_order": {"status": "FILLED_SIMULATED"},
            },
        )
        evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(_event())
        self.assertFalse(evidence.analysis_present)
        self.assertFalse(evidence.strategy_present)
        self.assertFalse(evidence.risk_present)
        self.assertEqual(evidence.execution_outcome, WorkflowExecutionOutcome.NOT_STARTED)

    def test_missing_current_expectation_ignores_versioned_downstream_evidence(self):
        release_id = "tracked:tracked-123"
        client = _Client(
            tables={
                "event_ai_analyses": [
                    {"id": "analysis-old", "event_id": release_id, "expectation_version": 1}
                ]
            },
            paper_state={
                "status": "expired_no_trade",
                "expectation_version": 1,
                "strategy": None,
                "risk": None,
            },
        )
        evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(_event())
        self.assertFalse(evidence.analysis_present)
        self.assertFalse(evidence.strategy_present)
        self.assertFalse(evidence.risk_present)
        self.assertEqual(evidence.execution_outcome, WorkflowExecutionOutcome.NOT_STARTED)

    def test_release_document_completion_survives_downstream_analysis_error(self):
        release_id = "tracked:tracked-123"
        client = _Client(
            tables={
                "current_event_expectations": [_current_expectation(release_id)],
                "event_source_documents": [{"id": "doc-1", "event_id": release_id}],
                "event_ingestion_runs": [
                    {
                        "event_id": release_id,
                        "status": "error",
                        "error_message": "AI analysis failed",
                        "created_at": "2026-08-28T06:05:00+00:00",
                    }
                ],
            }
        )
        evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(_event())
        self.assertTrue(evidence.release_document_present)
        self.assertFalse(evidence.release_failed)
        self.assertFalse(evidence.analysis_present)

    def test_error_without_release_document_requires_action(self):
        release_id = "tracked:tracked-123"
        client = _Client(
            tables={
                "current_event_expectations": [_current_expectation(release_id)],
                "event_ingestion_runs": [
                    {
                        "event_id": release_id,
                        "status": "error",
                        "error_message": "provider failed before document persistence",
                        "created_at": "2026-08-28T06:05:00+00:00",
                    }
                ],
            }
        )
        evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(_event())
        self.assertFalse(evidence.release_document_present)
        self.assertTrue(evidence.release_failed)

    def test_normal_no_release_does_not_require_action_before_overdue_marker(self):
        release_id = "tracked:tracked-123"
        client = _Client(
            tables={
                "current_event_expectations": [_current_expectation(release_id)],
                "event_ingestion_runs": [
                    {
                        "event_id": release_id,
                        "status": "no_release",
                        "error_message": "results_page selection no_match",
                        "created_at": "2026-08-28T06:00:00+00:00",
                    }
                ],
            }
        )
        evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(_event())
        self.assertFalse(evidence.release_failed)

    def test_overdue_no_release_requires_action(self):
        release_id = "tracked:tracked-123"
        client = _Client(
            tables={
                "current_event_expectations": [_current_expectation(release_id)],
                "event_ingestion_runs": [
                    {
                        "event_id": release_id,
                        "status": "no_release",
                        "error_message": "release overdue: scheduled_date=2026-08-28 still no_release",
                        "created_at": "2026-08-29T06:00:00+00:00",
                    }
                ],
            }
        )
        evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(_event())
        self.assertTrue(evidence.release_failed)

    def test_expired_no_trade_closes_execution_without_inventing_strategy_or_risk(self):
        release_id = "tracked:tracked-123"
        client = _Client(
            tables={"current_event_expectations": [_current_expectation(release_id)]},
            paper_state={
                "status": "expired_no_trade",
                "expectation_version": 2,
                "strategy": None,
                "risk": None,
            },
        )
        evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(_event())
        self.assertFalse(evidence.strategy_present)
        self.assertFalse(evidence.risk_present)
        self.assertEqual(evidence.execution_outcome, WorkflowExecutionOutcome.NO_TRADE)

    def test_unknown_terminal_paper_status_fails_closed(self):
        release_id = "tracked:tracked-123"
        client = _Client(
            tables={"current_event_expectations": [_current_expectation(release_id)]},
            paper_state={"status": "mystery_terminal", "expectation_version": 2},
        )
        with self.assertRaisesRegex(RuntimeError, "unsupported persisted paper status"):
            SupabaseWorkflowReadinessEvidenceLoader(client).load(_event())


if __name__ == "__main__":
    unittest.main()
