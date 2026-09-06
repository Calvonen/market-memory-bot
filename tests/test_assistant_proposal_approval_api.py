from __future__ import annotations

import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from trading_system.assistant_event_proposal import (
    AssistantProposalLiveLocked,
    AssistantProposalPreparationApprovalConflict,
)
from trading_system.assistant_proposal_api import build_assistant_proposal_router
from trading_system.assistant_proposal_approval_service import (
    AssistantPreparationApprovalResult,
    AssistantProposalApprovalService,
    MAX_PREPARATION_REVIEWER_LENGTH,
)
from trading_system.assistant_proposal_materializer import AssistantProposalMaterializationResult


PROPOSAL_ID = "00000000-0000-0000-0000-000000000324"
EXPECTED_REVIEW_ROUND = 2


class FakeApprovalService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.error: Exception | None = None

    def approve_preparation(
        self, proposal_id: str, *, reviewer: str, expected_review_round: int
    ):
        self.calls.append((proposal_id, reviewer, expected_review_round))
        if self.error is not None:
            raise self.error
        return AssistantPreparationApprovalResult(
            proposal_id=proposal_id,
            status="materialized",
            review_round=expected_review_round,
            event_id="tracked:event-324",
            tracked_event_id="event-324",
            expectation_version=3,
            retried=False,
        )


class FakeProposals:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.status = "approved_for_materialization"
        self.review_round = EXPECTED_REVIEW_ROUND
        self.error: Exception | None = None

    def approve_for_materialization(
        self, proposal_id: str, *, reviewer: str, expected_review_round: int
    ):
        self.calls.append((proposal_id, reviewer, expected_review_round))
        if self.error is not None:
            raise self.error
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


def approval_json(reviewer: str = "marko", review_round: int = EXPECTED_REVIEW_ROUND) -> dict:
    return {"reviewer": reviewer, "expected_review_round": review_round}


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
            json=approval_json(),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(control_calls, [None])
        self.assertEqual(service.calls, [])

    def test_expected_review_round_is_required(self) -> None:
        client, service, _ = self._client()
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json={"reviewer": "marko"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(service.calls, [])

    def test_success_binds_to_displayed_review_round_and_grants_no_trading_authority(self) -> None:
        client, service, _ = self._client()
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json=approval_json(" marko "),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(service.calls, [(PROPOSAL_ID, "marko", EXPECTED_REVIEW_ROUND)])
        self.assertEqual(payload["review_round"], EXPECTED_REVIEW_ROUND)
        self.assertIs(payload["trading_authority_granted"], False)

    def test_stale_review_round_conflict_is_409(self) -> None:
        client, service, _ = self._client()
        service.error = AssistantProposalPreparationApprovalConflict(
            "assistant_proposal_review_round_conflict"
        )
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json=approval_json(review_round=1),
        )
        self.assertEqual(response.status_code, 409)

    def test_reviewer_at_audit_limit_is_accepted(self) -> None:
        client, service, _ = self._client()
        reviewer = "r" * MAX_PREPARATION_REVIEWER_LENGTH
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json=approval_json(reviewer),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.calls, [(PROPOSAL_ID, reviewer, EXPECTED_REVIEW_ROUND)])

    def test_reviewer_over_audit_limit_is_rejected_before_service_call(self) -> None:
        client, service, _ = self._client()
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json=approval_json("r" * (MAX_PREPARATION_REVIEWER_LENGTH + 1)),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(service.calls, [])

    def test_locked_live_proposal_is_a_conflict(self) -> None:
        client, service, _ = self._client()
        service.error = AssistantProposalLiveLocked("LIVE proposal materialization is locked")
        response = client.post(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}/approve-preparation",
            headers={"X-MarketAI-Control-Key": "control-secret"},
            json=approval_json(),
        )
        self.assertEqual(response.status_code, 409)

    def test_service_passes_expected_round_before_materializing(self) -> None:
        proposals = FakeProposals()
        materializer = FakeMaterializer()
        service = AssistantProposalApprovalService(proposals=proposals, materializer=materializer)

        result = service.approve_preparation(
            PROPOSAL_ID,
            reviewer="marko",
            expected_review_round=EXPECTED_REVIEW_ROUND,
        )

        self.assertEqual(
            proposals.calls,
            [(PROPOSAL_ID, "marko", EXPECTED_REVIEW_ROUND)],
        )
        self.assertEqual(materializer.calls, [PROPOSAL_ID])
        self.assertEqual(result.review_round, EXPECTED_REVIEW_ROUND)

    def test_service_does_not_materialize_when_round_conflicts(self) -> None:
        proposals = FakeProposals()
        proposals.error = AssistantProposalPreparationApprovalConflict(
            "assistant_proposal_review_round_conflict"
        )
        materializer = FakeMaterializer()
        service = AssistantProposalApprovalService(proposals=proposals, materializer=materializer)

        with self.assertRaises(AssistantProposalPreparationApprovalConflict):
            service.approve_preparation(
                PROPOSAL_ID,
                reviewer="marko",
                expected_review_round=1,
            )

        self.assertEqual(materializer.calls, [])

    def test_service_rejects_overlong_reviewer_before_repository_call(self) -> None:
        proposals = FakeProposals()
        materializer = FakeMaterializer()
        service = AssistantProposalApprovalService(proposals=proposals, materializer=materializer)

        with self.assertRaises(ValueError):
            service.approve_preparation(
                PROPOSAL_ID,
                reviewer="r" * (MAX_PREPARATION_REVIEWER_LENGTH + 1),
                expected_review_round=EXPECTED_REVIEW_ROUND,
            )

        self.assertEqual(proposals.calls, [])
        self.assertEqual(materializer.calls, [])


if __name__ == "__main__":
    unittest.main()
