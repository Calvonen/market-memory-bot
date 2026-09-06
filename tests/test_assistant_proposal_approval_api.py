from __future__ import annotations

import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from trading_system.assistant_event_proposal import AssistantProposalLiveLocked
from trading_system.assistant_proposal_api import build_assistant_proposal_router
from trading_system.assistant_proposal_approval_service import (
    AssistantPreparationApprovalResult,
    AssistantProposalApprovalService,
    MAX_PREPARATION_REVIEWER_LENGTH,
)
from trading_system.assistant_proposal_materializer import (
    AssistantProposalMaterializationResult,
)


PROPOSAL_ID = "00000000-0000-0000-0000-000000000324"


class FakeApprovalService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error: Exception | None = None

    def approve_preparation(self, proposal_id: str, *, reviewer: str):
        self.calls.append((proposal_id, reviewer))
        if self.error is not None:
            raise self.error
        return AssistantPreparationApprovalResult(
            proposal_id=proposal_id,
            status="materialized",
            review_round=2,
            event_id="tracked:event-324",
            tracked_event_id="event-324",
            expectation_version=3,
            retried=False,
        )


class FakeProposals:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.status = "approved_for_materialization"
        self.review_round = 2

    def approve_for_materialization(self, proposal_id: str, *, reviewer: str):
        self.calls.append((proposal_id, reviewer))
        return self.status, self.review_round


class FakeMaterializer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def materialize(self, proposal_id: str) -> AssistantProposalMaterializationResult:
        self.calls.append(proposal_id)
        return AssistantProposalMaterializationResult(
            proposal_id=proposal_id,
            tracked_event_id="event-324",
            event_id="tracked:event-324",
            expectation_version=3,
            retried=False,
        )


class AssistantProposalApprovalApiTests(unittest.TestCase):
    def _client(self):
        service = FakeApprovalService()
        control_calls: list[str | None] = []

        def require_control(value: str | None) -> None:
            control_calls.append(value)
            if value != "control-secret":
                raise HTTPException(status_code=401, detail="Invalid control key")

        app = FastAPI()
        app.include_router(
            build_assistant_proposal_router(
                require_control=require_control,
                get_approval_service=lambda: service,
            )
        )
        return TestClient(app), service, control_calls

    def test_control_key_is_required_before_service_call(self) -> None:
        client, service, control_calls = self._client()
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            json={"reviewer": "marko"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(control_calls, [None])
        self.assertEqual(service.calls, [])

    def test_success_explicitly_grants_no_trading_authority(self) -> None:
        client, service, _ = self._client()
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json={"reviewer": " marko "},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(service.calls, [(PROPOSAL_ID, "marko")])
        self.assertEqual(payload["status"], "materialized")
        self.assertEqual(payload["event_id"], "tracked:event-324")
        self.assertEqual(payload["expectation_version"], 3)
        self.assertIs(payload["trading_authority_granted"], False)

    def test_reviewer_at_audit_limit_is_accepted(self) -> None:
        client, service, _ = self._client()
        reviewer = "r" * MAX_PREPARATION_REVIEWER_LENGTH
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json={"reviewer": reviewer},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.calls, [(PROPOSAL_ID, reviewer)])

    def test_reviewer_over_audit_limit_is_rejected_before_service_call(self) -> None:
        client, service, _ = self._client()
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json={"reviewer": "r" * (MAX_PREPARATION_REVIEWER_LENGTH + 1)},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(service.calls, [])

    def test_locked_live_proposal_is_a_conflict_not_retryable_service_failure(self) -> None:
        client, service, _ = self._client()
        service.error = AssistantProposalLiveLocked("LIVE proposal materialization is locked")
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json={"reviewer": "marko"},
        )
        self.assertEqual(response.status_code, 409)

    def test_service_approves_preparation_before_materializing(self) -> None:
        proposals = FakeProposals()
        materializer = FakeMaterializer()
        service = AssistantProposalApprovalService(
            proposals=proposals, materializer=materializer
        )

        result = service.approve_preparation(PROPOSAL_ID, reviewer="marko")

        self.assertEqual(proposals.calls, [(PROPOSAL_ID, "marko")])
        self.assertEqual(materializer.calls, [PROPOSAL_ID])
        self.assertEqual(result.status, "materialized")
        self.assertEqual(result.review_round, 2)
        self.assertFalse(result.retried)

    def test_service_rejects_overlong_reviewer_before_repository_call(self) -> None:
        proposals = FakeProposals()
        materializer = FakeMaterializer()
        service = AssistantProposalApprovalService(
            proposals=proposals, materializer=materializer
        )

        with self.assertRaises(ValueError):
            service.approve_preparation(
                PROPOSAL_ID,
                reviewer="r" * (MAX_PREPARATION_REVIEWER_LENGTH + 1),
            )

        self.assertEqual(proposals.calls, [])
        self.assertEqual(materializer.calls, [])

    def test_service_marks_retry_when_proposal_was_already_materialized(self) -> None:
        proposals = FakeProposals()
        proposals.status = "materialized"
        materializer = FakeMaterializer()
        service = AssistantProposalApprovalService(
            proposals=proposals, materializer=materializer
        )

        result = service.approve_preparation(PROPOSAL_ID, reviewer="marko")

        self.assertTrue(result.retried)


if __name__ == "__main__":
    unittest.main()
