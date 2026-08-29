from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_system.tracked_event_release_skip import (
    ReleaseSkipNotFound,
    SupabaseTrackedEventReleaseSkipAuditRepository,
    skip_tracked_event_release,
)
from trading_system.tracked_event_release_skip_api import (
    build_tracked_event_release_skip_router,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)

EVENT_ID = "11111111-1111-1111-1111-111111111111"
RELEASE_ID = f"tracked:{EVENT_ID}"


def _event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id=EVENT_ID,
        tracked_instrument_id="22222222-2222-2222-2222-222222222222",
        calendar_event_id=None,
        company_name="Example plc",
        instrument="EX.L",
        market="LSE",
        source="manual",
        external_key="example-results",
        kind="earnings",
        title="Results",
        event_at=datetime(2026, 8, 20, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.TRACKED,
    )


class _Audit:
    def __init__(self):
        self.calls = []

    def record_skip(self, **values):
        self.calls.append(values)


class _Events:
    def get(self, event_id):
        return _event() if event_id == EVENT_ID else None


class _RpcResult:
    def execute(self):
        raise RuntimeError("P0002: tracked_event_not_found")


class _RpcClient:
    def rpc(self, name, params):
        return _RpcResult()


def test_service_records_only_canonical_identity_and_operator_audit() -> None:
    audit = _Audit()
    result = skip_tracked_event_release(
        _event(),
        audit_repository=audit,
        actor="operator@example.com",
        reason="Release is not relevant to this tracked event",
    )

    assert result.status == "skipped"
    assert result.release_event_id == RELEASE_ID
    assert audit.calls == [
        {
            "tracked_event_id": EVENT_ID,
            "release_event_id": RELEASE_ID,
            "actor": "operator@example.com",
            "reason": "Release is not relevant to this tracked event",
        }
    ]


def test_supabase_repository_classifies_atomic_not_found() -> None:
    repository = SupabaseTrackedEventReleaseSkipAuditRepository(_RpcClient())

    with pytest.raises(ReleaseSkipNotFound):
        repository.record_skip(
            tracked_event_id=EVENT_ID,
            release_event_id=RELEASE_ID,
            actor="operator",
            reason="Not applicable",
        )


def _client(*, audit=None) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_tracked_event_release_skip_router(
            require_control=lambda key: None,
            get_tracked_event_repository=lambda: _Events(),
            get_release_skip_audit_repository=lambda: audit or _Audit(),
            get_release_shell_repository=lambda: (_ for _ in ()).throw(
                AssertionError("mutating shell validator must not be called")
            ),
        )
    )
    return TestClient(app)


def test_api_rejects_malformed_uuid_before_repository_access() -> None:
    response = _client().post(
        "/api/v1/tracked-events/not-a-uuid/release-skip",
        headers={"X-MarketAI-Control-Key": "control", "X-MarketAI-Actor": "operator"},
        json={"reason": "Not applicable"},
    )
    assert response.status_code == 400


def test_api_rejects_blank_actor_and_reason() -> None:
    client = _client()
    headers = {"X-MarketAI-Control-Key": "control", "X-MarketAI-Actor": " "}
    assert (
        client.post(
            f"/api/v1/tracked-events/{EVENT_ID}/release-skip",
            headers=headers,
            json={"reason": "Not applicable"},
        ).status_code
        == 422
    )
    headers["X-MarketAI-Actor"] = "operator"
    assert (
        client.post(
            f"/api/v1/tracked-events/{EVENT_ID}/release-skip",
            headers=headers,
            json={"reason": " "},
        ).status_code
        == 422
    )


def test_api_maps_atomic_not_found_to_404() -> None:
    class _GoneAudit:
        def record_skip(self, **values):
            raise ReleaseSkipNotFound("tracked_event_not_found")

    response = _client(audit=_GoneAudit()).post(
        f"/api/v1/tracked-events/{EVENT_ID}/release-skip",
        headers={"X-MarketAI-Control-Key": "control", "X-MarketAI-Actor": "operator"},
        json={"reason": "Not applicable"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Tracked event not found"
