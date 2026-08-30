from __future__ import annotations

from datetime import UTC, datetime

from trading_system.event_workflow import EARNINGS_WORKFLOW, WorkflowStepKey, WorkflowStepStatus
from trading_system.event_workflow_readiness import (
    WorkflowReadinessEvidence,
    project_workflow_readiness,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)
from trading_system.workflow_readiness_evidence_loader import (
    SupabaseWorkflowReadinessEvidenceLoader,
)


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self.order_field = None
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
        if self.order_field is not None:
            rows.sort(key=lambda row: row.get(self.order_field) or "", reverse=self.desc)
        if self.limit_count is not None:
            rows = rows[: self.limit_count]
        return _Response(rows)


class _RpcQuery:
    def execute(self):
        return _Response([])


class _Client:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Query(self.tables.get(name, []))

    def rpc(self, name, params):
        assert name == "get_event_paper_trade_state"
        assert params == {"input_event_id": "tracked:11111111-1111-1111-1111-111111111111"}
        return _RpcQuery()


def _event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="11111111-1111-1111-1111-111111111111",
        tracked_instrument_id="22222222-2222-2222-2222-222222222222",
        calendar_event_id=None,
        company_name="Example Plc",
        instrument="EXM",
        market="USA",
        source="manual",
        external_key="example-results",
        kind="earnings",
        title="Example earnings",
        event_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.MONITORING,
    )


def _release_status(evidence: WorkflowReadinessEvidence) -> WorkflowStepStatus:
    states = project_workflow_readiness(EARNINGS_WORKFLOW, evidence)
    return next(state.status for state in states if state.key is WorkflowStepKey.RELEASE)


def test_audited_skip_projects_release_as_skipped() -> None:
    evidence = WorkflowReadinessEvidence(
        tracked_status=TrackedEventStatus.MONITORING,
        release_skipped=True,
    )
    assert _release_status(evidence) is WorkflowStepStatus.SKIPPED


def test_persisted_release_document_wins_over_older_skip_evidence() -> None:
    evidence = WorkflowReadinessEvidence(
        tracked_status=TrackedEventStatus.MONITORING,
        release_document_present=True,
        release_skipped=True,
    )
    assert _release_status(evidence) is WorkflowStepStatus.COMPLETED


def test_loader_uses_only_matching_canonical_skip_and_suppresses_release_blocker() -> None:
    event = _event()
    release_id = f"tracked:{event.event_id}"
    client = _Client(
        {
            "current_event_expectations": [{"event_id": release_id, "version": 1}],
            "tracked_event_release_skip_audit": [
                {
                    "id": 1,
                    "tracked_event_id": event.event_id,
                    "release_event_id": release_id,
                    "created_at": "2026-08-29T12:05:00+00:00",
                },
                {
                    "id": 2,
                    "tracked_event_id": event.event_id,
                    "release_event_id": "tracked:other",
                    "created_at": "2026-08-29T12:06:00+00:00",
                },
            ],
            "tracked_event_workflow_blockers": [
                {
                    "tracked_market_event_id": event.event_id,
                    "step_key": "release",
                    "blocker_code": "release_source_missing",
                    "message": "Approved source is missing",
                    "resolved_at": None,
                    "updated_at": "2026-08-29T12:00:00+00:00",
                }
            ],
        }
    )

    evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(event)

    assert evidence.release_skipped is True
    assert evidence.release_failed is False
    assert evidence.release_action_code is None
    assert evidence.release_action_reason is None
    assert _release_status(evidence) is WorkflowStepStatus.SKIPPED


def test_loader_document_created_after_skip_wins_even_with_unresolved_blocker() -> None:
    event = _event()
    release_id = f"tracked:{event.event_id}"
    client = _Client(
        {
            "current_event_expectations": [{"event_id": release_id, "version": 1}],
            "tracked_event_release_skip_audit": [
                {
                    "id": 1,
                    "tracked_event_id": event.event_id,
                    "release_event_id": release_id,
                    "created_at": "2026-08-29T12:05:00+00:00",
                }
            ],
            "event_source_documents": [
                {
                    "id": "doc-1",
                    "event_id": release_id,
                    "created_at": "2026-08-29T12:10:00+00:00",
                }
            ],
            "tracked_event_workflow_blockers": [
                {
                    "tracked_market_event_id": event.event_id,
                    "step_key": "release",
                    "blocker_code": "release_source_missing",
                    "message": "Approved source is missing",
                    "resolved_at": None,
                    "updated_at": "2026-08-29T12:00:00+00:00",
                }
            ],
        }
    )

    evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(event)

    assert evidence.release_skipped is True
    assert evidence.release_document_present is True
    assert evidence.release_failed is False
    assert _release_status(evidence) is WorkflowStepStatus.COMPLETED


def test_loader_document_older_than_skip_remains_masked_by_unresolved_blocker() -> None:
    event = _event()
    release_id = f"tracked:{event.event_id}"
    client = _Client(
        {
            "current_event_expectations": [{"event_id": release_id, "version": 1}],
            "tracked_event_release_skip_audit": [
                {
                    "id": 1,
                    "tracked_event_id": event.event_id,
                    "release_event_id": release_id,
                    "created_at": "2026-08-29T12:05:00+00:00",
                }
            ],
            "event_source_documents": [
                {
                    "id": "doc-old",
                    "event_id": release_id,
                    "created_at": "2026-08-29T11:55:00+00:00",
                }
            ],
            "tracked_event_workflow_blockers": [
                {
                    "tracked_market_event_id": event.event_id,
                    "step_key": "release",
                    "blocker_code": "release_source_missing",
                    "message": "Approved source is missing",
                    "resolved_at": None,
                    "updated_at": "2026-08-29T12:00:00+00:00",
                }
            ],
        }
    )

    evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(event)

    assert evidence.release_document_present is False
    assert evidence.release_skipped is True
    assert _release_status(evidence) is WorkflowStepStatus.SKIPPED


def test_loader_does_not_accept_skip_for_different_release_identity() -> None:
    event = _event()
    release_id = f"tracked:{event.event_id}"
    client = _Client(
        {
            "current_event_expectations": [{"event_id": release_id, "version": 1}],
            "tracked_event_release_skip_audit": [
                {
                    "id": 1,
                    "tracked_event_id": event.event_id,
                    "release_event_id": "tracked:other",
                    "created_at": "2026-08-29T12:05:00+00:00",
                }
            ],
        }
    )

    evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(event)

    assert evidence.release_skipped is False
    assert _release_status(evidence) is WorkflowStepStatus.PENDING


def test_loader_does_not_accept_skip_for_different_tracked_event_identity() -> None:
    event = _event()
    release_id = f"tracked:{event.event_id}"
    client = _Client(
        {
            "current_event_expectations": [{"event_id": release_id, "version": 1}],
            "tracked_event_release_skip_audit": [
                {
                    "id": 1,
                    "tracked_event_id": "33333333-3333-3333-3333-333333333333",
                    "release_event_id": release_id,
                    "created_at": "2026-08-29T12:05:00+00:00",
                }
            ],
        }
    )

    evidence = SupabaseWorkflowReadinessEvidenceLoader(client).load(event)

    assert evidence.release_skipped is False
    assert _release_status(evidence) is WorkflowStepStatus.PENDING
