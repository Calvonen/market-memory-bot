from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_system.tracked_event_release_skip import skip_tracked_event_release
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


class _Shell:
    def ensure_release_shell(self, event):
        return RELEASE_ID


class _Audit:
    def __init__(self):
        self.calls = []

    def record_skip(self, **values):
        self.calls.append(values)


class _Events:
    def get(self, event_id):
        return _event() if event_id == EVENT_ID else None


def test_service_records_only_canonical_identity_and_operator_audit() -> None:
    audit = _Audit()
    result = skip_tracked_event_release(
        _event(),
        release_shell_repository=_Shell(),
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


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(
        build_tracked_event_release_skip_router(
            require_control=lambda key: None,
            get_tracked_event_repository=lambda: _Events(),
            get_release_shell_repository=lambda: _Shell(),
            get_release_skip_audit_repository=lambda: _Audit(),
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
