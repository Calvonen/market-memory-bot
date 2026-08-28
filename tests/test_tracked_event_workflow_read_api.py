from __future__ import annotations

import unittest
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.event_workflow_readiness import (
    WorkflowExecutionOutcome,
    WorkflowReadinessEvidence,
)
from trading_system.models import TradingMode
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


EVENT_ID = "e6b0325a-6cf4-42e0-b94a-f5e84594e4da"


class _TrackedEventRepository:
    def __init__(self, event: PersistentTrackedEvent | None) -> None:
        self.event = event
        self.get_calls: list[str] = []

    def get(self, event_id: str) -> PersistentTrackedEvent | None:
        self.get_calls.append(event_id)
        return self.event if event_id.replace("-", "") == EVENT_ID.replace("-", "") else None


class _EvidenceLoader:
    def __init__(
        self,
        evidence: WorkflowReadinessEvidence,
        error: Exception | None = None,
    ) -> None:
        self.evidence = evidence
        self.error = error
        self.calls: list[str] = []

    def load(self, event: PersistentTrackedEvent) -> WorkflowReadinessEvidence:
        self.calls.append(event.event_id)
        if self.error is not None:
            raise self.error
        return self.evidence


def _event(*, kind: str = "earnings") -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id=EVENT_ID,
        tracked_instrument_id="tracked-adsk",
        calendar_event_id="24b20d87-f7cf-408f-80e4-b7a94624229f",
        company_name="AUTODESK INC",
        instrument="ADSK",
        market="USA",
        source="finnhub",
        external_key="calendar:24b20d87-f7cf-408f-80e4-b7a94624229f",
        kind=kind,
        title="AUTODESK INC earnings",
        event_at=datetime(2026, 8, 27, 20, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=TrackedEventStatus.MONITORING,
    )


def _evidence(
    *,
    release_document_present: bool = True,
    release_failed: bool = False,
    analysis_present: bool = True,
    reaction_present: bool = True,
) -> WorkflowReadinessEvidence:
    return WorkflowReadinessEvidence(
        event_id=EVENT_ID,
        tracked_status=TrackedEventStatus.MONITORING,
        release_document_present=release_document_present,
        release_failed=release_failed,
        analysis_present=analysis_present,
        reaction_present=reaction_present,
        strategy_present=True,
        risk_present=True,
        execution_outcome=WorkflowExecutionOutcome.FILLED,
        trading_mode=TradingMode.PAPER,
    )


class TrackedEventWorkflowReadApiTests(unittest.TestCase):
    def _client(
        self,
        repository: _TrackedEventRepository,
        loader: _EvidenceLoader,
    ) -> TestClient:
        return TestClient(
            create_app(
                tracked_event_repository=repository,
                workflow_evidence_loader=loader,
                read_api_key="read-secret",
            )
        )

    def test_requires_read_key_before_backend_access(self):
        repository = _TrackedEventRepository(_event())
        loader = _EvidenceLoader(_evidence())
        client = self._client(repository, loader)

        response = client.get(f"/api/v1/tracked-events/{EVENT_ID}/workflow")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(repository.get_calls, [])
        self.assertEqual(loader.calls, [])

    def test_rejects_malformed_event_id_before_backend_access(self):
        repository = _TrackedEventRepository(_event())
        loader = _EvidenceLoader(_evidence())
        client = self._client(repository, loader)

        response = client.get(
            "/api/v1/tracked-events/not-a-uuid/workflow",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "event_id must be a valid UUID")
        self.assertEqual(repository.get_calls, [])
        self.assertEqual(loader.calls, [])

    def test_returns_canonical_observation_workflow(self):
        repository = _TrackedEventRepository(_event())
        loader = _EvidenceLoader(_evidence())
        client = self._client(repository, loader)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/workflow",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(loader.calls, [EVENT_ID])
        self.assertEqual(
            response.json(),
            {
                "event_id": EVENT_ID,
                "profile_id": "earnings_documented_observation_v1",
                "trading_mode": None,
                "steps": [
                    {"key": "tracking", "mode": "required", "status": "running"},
                    {"key": "event_identified", "mode": "required", "status": "completed"},
                    {"key": "release", "mode": "required", "status": "completed"},
                    {"key": "analysis", "mode": "required", "status": "completed"},
                    {"key": "market_reaction", "mode": "required", "status": "running"},
                ],
            },
        )

    def test_persisted_paper_progress_does_not_turn_tracking_into_trade_workflow(self):
        repository = _TrackedEventRepository(_event())
        loader = _EvidenceLoader(_evidence())
        client = self._client(repository, loader)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/workflow",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["trading_mode"])
        self.assertNotIn("strategy", [step["key"] for step in payload["steps"]])
        self.assertNotIn("risk", [step["key"] for step in payload["steps"]])
        self.assertNotIn("paper", [step["key"] for step in payload["steps"]])

    def test_content_event_skips_release_stage(self):
        repository = _TrackedEventRepository(_event(kind="news"))
        loader = _EvidenceLoader(
            _evidence(
                release_document_present=False,
                analysis_present=True,
                reaction_present=True,
            )
        )
        client = self._client(repository, loader)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/workflow",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["profile_id"], "content_event_observation_v1")
        release = next(step for step in payload["steps"] if step["key"] == "release")
        self.assertEqual(release, {"key": "release", "mode": "skip", "status": "skipped"})

    def test_release_failure_is_action_required(self):
        repository = _TrackedEventRepository(_event())
        loader = _EvidenceLoader(
            _evidence(
                release_document_present=False,
                release_failed=True,
                analysis_present=False,
            )
        )
        client = self._client(repository, loader)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/workflow",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        release = next(step for step in response.json()["steps"] if step["key"] == "release")
        self.assertEqual(release["status"], "action_required")

    def test_missing_event_is_404_without_evidence_read(self):
        repository = _TrackedEventRepository(None)
        loader = _EvidenceLoader(_evidence())
        client = self._client(repository, loader)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/workflow",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Tracked event not found")
        self.assertEqual(loader.calls, [])

    def test_evidence_failure_is_503(self):
        repository = _TrackedEventRepository(_event())
        loader = _EvidenceLoader(_evidence(), RuntimeError("workflow evidence read failed"))
        client = self._client(repository, loader)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/workflow",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "workflow evidence read failed")


if __name__ == "__main__":
    unittest.main()
