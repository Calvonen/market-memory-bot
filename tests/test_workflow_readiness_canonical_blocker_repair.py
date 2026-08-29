from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)
from trading_system.workflow_readiness_evidence_loader import (
    SupabaseWorkflowReadinessEvidenceLoader,
)


RELEASE_ID = "tracked:tracked-123"


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
            rows.sort(
                key=lambda row: row.get(self.order_field) or "",
                reverse=self.desc,
            )
        if self.limit_count is not None:
            rows = rows[: self.limit_count]
        return _Response(rows)


class _RpcQuery:
    def execute(self):
        return _Response([])


class _Client:
    def __init__(self, *, analysis_version: int | None):
        analysis_rows = []
        if analysis_version is not None:
            analysis_rows.append(
                {
                    "id": "analysis-1",
                    "event_id": RELEASE_ID,
                    "expectation_version": analysis_version,
                }
            )
        self.tables = {
            "current_event_expectations": [
                {"event_id": RELEASE_ID, "version": 2}
            ],
            "event_source_documents": [
                {"id": "doc-1", "event_id": RELEASE_ID}
            ],
            "event_ai_analyses": analysis_rows,
            "event_ingestion_runs": [
                {
                    "event_id": RELEASE_ID,
                    "provider": "canonical_release_worker",
                    "status": "error",
                    "error_message": "action_required: release-shell identity mismatch",
                    "created_at": "2026-08-28T06:10:00+00:00",
                }
            ],
            "tracked_market_event_reactions": [],
        }

    def table(self, name):
        return _Query(self.tables.get(name, []))

    def rpc(self, name, params):
        if name != "get_event_paper_trade_state":
            raise AssertionError(name)
        return _RpcQuery()


def _event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="tracked-123",
        tracked_instrument_id="instrument-1",
        calendar_event_id=None,
        company_name="Example Plc",
        instrument="EXM",
        market="USA",
        source="manual",
        external_key="example",
        kind="earnings",
        title="Example earnings",
        event_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.MONITORING,
    )


class CanonicalBlockerRepairTests(unittest.TestCase):
    def test_current_version_analysis_supersedes_stale_canonical_blocker(self):
        evidence = SupabaseWorkflowReadinessEvidenceLoader(
            _Client(analysis_version=2)
        ).load(_event())

        self.assertTrue(evidence.analysis_present)
        self.assertTrue(evidence.release_document_present)
        self.assertFalse(evidence.release_failed)

    def test_stale_analysis_does_not_supersede_current_canonical_blocker(self):
        evidence = SupabaseWorkflowReadinessEvidenceLoader(
            _Client(analysis_version=1)
        ).load(_event())

        self.assertFalse(evidence.analysis_present)
        self.assertFalse(evidence.release_document_present)
        self.assertTrue(evidence.release_failed)


if __name__ == "__main__":
    unittest.main()
