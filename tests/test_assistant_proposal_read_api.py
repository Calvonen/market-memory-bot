from __future__ import annotations

import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from trading_system.assistant_proposal_api import build_assistant_proposal_router
from trading_system.assistant_proposal_read_repository import AssistantProposalReadRecord


PROPOSAL_ID = "00000000-0000-0000-0000-000000000326"


def _record(status: str = "ready_for_review") -> AssistantProposalReadRecord:
    return AssistantProposalReadRecord(
        id=PROPOSAL_ID,
        proposal_key="SYR.ASX:earnings:2026-09-07",
        payload={
            "company_name": "Syrah Resources Limited",
            "instrument": "SYR.ASX",
            "market": "Australia",
            "kind": "earnings",
            "title": "Syrah Resources results",
            "scheduled_date": "2026-09-07",
            "event_at": "2026-09-07T00:00:00+00:00",
            "event_time_status": "unknown",
            "base_expectation_version": 1,
            "official_source": {
                "source_kind": "results_page",
                "source_url": "https://www.syrahresources.com.au/investors/reports-presentations",
            },
            "strategy": {"instrument": "SYR.ASX", "scheduled_date": "2026-09-07"},
        },
        requested_execution_mode="demo",
        status=status,
        reviewed_by=None,
        review_round=0,
        created_at="2026-09-06T10:00:00+00:00",
        updated_at="2026-09-06T10:00:00+00:00",
    )


class FakeReadRepository:
    def __init__(self) -> None:
        self.list_calls: list[tuple[str | None, int]] = []
        self.get_calls: list[str] = []
        self.list_result = [_record()]
        self.get_result = _record()
        self.list_error: Exception | None = None
        self.get_error: Exception | None = None

    def list(self, *, status: str | None = None, limit: int = 50):
        self.list_calls.append((status, limit))
        if self.list_error is not None:
            raise self.list_error
        return self.list_result

    def get(self, proposal_id: str):
        self.get_calls.append(proposal_id)
        if self.get_error is not None:
            raise self.get_error
        return self.get_result


class AssistantProposalReadApiTests(unittest.TestCase):
    def _client(self):
        repository = FakeReadRepository()
        approval_service_calls: list[bool] = []

        def require_read(value: str | None) -> None:
            if value != "read-secret":
                raise HTTPException(status_code=401, detail="Invalid read key")

        def get_approval_service():
            approval_service_calls.append(True)
            raise AssertionError("read path must not construct approval service")

        app = FastAPI()
        app.include_router(
            build_assistant_proposal_router(
                require_control=lambda _: None,
                get_approval_service=get_approval_service,
                require_read=require_read,
                get_read_repository=lambda: repository,
            )
        )
        return TestClient(app), repository, approval_service_calls

    def test_read_key_is_required_before_repository_call(self) -> None:
        client, repository, approval_calls = self._client()
        response = client.get("/api/v1/assistant-proposals")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(repository.list_calls, [])
        self.assertEqual(approval_calls, [])

    def test_default_list_uses_visible_status_set_in_repository(self) -> None:
        client, repository, approval_calls = self._client()
        response = client.get(
            "/api/v1/assistant-proposals",
            headers={"X-MarketAI-Key": "read-secret"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(repository.list_calls, [(None, 50)])
        self.assertEqual(response.json()[0]["status"], "ready_for_review")
        self.assertEqual(response.json()[0]["payload"]["instrument"], "SYR.ASX")
        self.assertEqual(approval_calls, [])

    def test_explicit_status_filter_and_limit_are_forwarded(self) -> None:
        client, repository, _ = self._client()
        response = client.get(
            "/api/v1/assistant-proposals?status=rejected&limit=7",
            headers={"X-MarketAI-Key": "read-secret"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(repository.list_calls, [("rejected", 7)])

    def test_detail_returns_full_review_payload_without_approval_service(self) -> None:
        client, repository, approval_calls = self._client()
        response = client.get(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}",
            headers={"X-MarketAI-Key": "read-secret"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(repository.get_calls, [PROPOSAL_ID])
        payload = response.json()
        self.assertEqual(payload["proposal_key"], "SYR.ASX:earnings:2026-09-07")
        self.assertEqual(payload["payload"]["official_source"]["source_kind"], "results_page")
        self.assertEqual(approval_calls, [])

    def test_missing_detail_returns_404(self) -> None:
        client, repository, _ = self._client()
        repository.get_result = None
        response = client.get(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}",
            headers={"X-MarketAI-Key": "read-secret"},
        )
        self.assertEqual(response.status_code, 404)

    def test_malformed_detail_id_is_422_before_repository_call(self) -> None:
        client, repository, _ = self._client()
        response = client.get(
            "/api/v1/assistant-proposals/bad",
            headers={"X-MarketAI-Key": "read-secret"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(repository.get_calls, [])

    def test_list_backend_failure_is_503(self) -> None:
        client, repository, _ = self._client()
        repository.list_error = Exception("postgrest unavailable")
        response = client.get(
            "/api/v1/assistant-proposals",
            headers={"X-MarketAI-Key": "read-secret"},
        )
        self.assertEqual(response.status_code, 503)

    def test_detail_backend_failure_is_503(self) -> None:
        client, repository, _ = self._client()
        repository.get_error = Exception("postgrest unavailable")
        response = client.get(
            f"/api/v1/assistant-proposals/{PROPOSAL_ID}",
            headers={"X-MarketAI-Key": "read-secret"},
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
