from __future__ import annotations

import unittest
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceVersionConflict,
    OfficialReleaseSourceState,
)
from trading_system.tracked_event_release_source_api import (
    build_tracked_event_release_source_router,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


EVENT_ID = "11111111-1111-1111-1111-111111111111"


def _event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id=EVENT_ID,
        tracked_instrument_id="22222222-2222-2222-2222-222222222222",
        calendar_event_id=None,
        company_name="Example Oyj",
        instrument="EXAMPLE.HE",
        market="Helsinki",
        source="manual",
        external_key="example-q2",
        kind="earnings",
        title="Q2 results",
        event_at=datetime(2026, 8, 29, 6, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.TRACKED,
    )


class _TrackedRepo:
    def __init__(self, event: PersistentTrackedEvent | None) -> None:
        self.event = event
        self.requested_event_ids: list[str] = []

    def get(self, event_id: str) -> PersistentTrackedEvent | None:
        self.requested_event_ids.append(event_id)
        return self.event if event_id == EVENT_ID else None


class _SourceRepo:
    def __init__(
        self,
        state: OfficialReleaseSourceState,
        *,
        set_error: Exception | None = None,
    ) -> None:
        self.state = state
        self.set_error = set_error
        self.requested_event_id: str | None = None
        self.set_call: tuple[OfficialReleaseSource, int, str] | None = None

    def get_state(self, event_id: str) -> OfficialReleaseSourceState:
        self.requested_event_id = event_id
        return self.state

    def set(
        self, source: OfficialReleaseSource, *, expected_version: int, actor: str
    ) -> OfficialReleaseSource:
        self.set_call = (source, expected_version, actor)
        if self.set_error is not None:
            raise self.set_error
        return OfficialReleaseSource(
            event_id=source.event_id,
            source_kind=source.source_kind,
            source_url=source.source_url,
            source_title=source.source_title,
            version=expected_version + 1,
        )


def _client(
    *,
    event: PersistentTrackedEvent | None,
    state: OfficialReleaseSourceState,
    read_key: str = "read-key",
    control_key: str = "control-key",
    set_error: Exception | None = None,
) -> tuple[TestClient, _TrackedRepo, _SourceRepo]:
    tracked_repo = _TrackedRepo(event)
    source_repo = _SourceRepo(state, set_error=set_error)

    def require_read(value: str | None) -> None:
        if value != read_key:
            raise HTTPException(status_code=401, detail="Invalid or missing read API key")

    def require_control(value: str | None) -> None:
        if value != control_key:
            raise HTTPException(status_code=401, detail="Invalid or missing control API key")

    app = FastAPI()
    app.include_router(
        build_tracked_event_release_source_router(
            require_read=require_read,
            require_control=require_control,
            get_tracked_event_repository=lambda: tracked_repo,
            get_official_release_source_repository=lambda: source_repo,
        )
    )
    return TestClient(app), tracked_repo, source_repo


class TrackedEventReleaseSourceApiTests(unittest.TestCase):
    def test_sets_source_with_canonical_identity_and_audited_cas(self) -> None:
        client, _, source_repo = _client(
            event=_event(), state=OfficialReleaseSourceState(source=None, version=0)
        )

        response = client.put(
            f"/api/v1/tracked-events/{EVENT_ID}/release-source",
            headers={
                "X-MarketAI-Control-Key": "control-key",
                "X-MarketAI-Actor": "  reviewer@example.com  ",
            },
            json={
                "source_kind": "direct_url",
                "source_url": "https://example.com/results.pdf",
                "source_title": "Q2 results",
                "expected_version": 0,
            },
        )

        self.assertEqual(response.status_code, 200)
        assert source_repo.set_call is not None
        source, expected_version, actor = source_repo.set_call
        self.assertEqual(source.event_id, f"tracked:{EVENT_ID}")
        self.assertEqual((expected_version, actor), (0, "reviewer@example.com"))
        self.assertEqual(
            response.json(),
            {
                "event_id": EVENT_ID,
                "release_event_id": f"tracked:{EVENT_ID}",
                "active": True,
                "version": 1,
                "source_kind": "direct_url",
                "source_url": "https://example.com/results.pdf",
                "source_title": "Q2 results",
            },
        )

    def test_put_requires_control_auth_and_actor(self) -> None:
        client, tracked_repo, _ = _client(
            event=_event(), state=OfficialReleaseSourceState(source=None, version=0)
        )
        payload = {
            "source_kind": "results_page",
            "source_url": "https://example.com/results",
            "expected_version": 0,
        }

        unauthorized = client.put(
            f"/api/v1/tracked-events/{EVENT_ID}/release-source",
            headers={"X-MarketAI-Actor": "actor"},
            json=payload,
        )
        missing_actor = client.put(
            f"/api/v1/tracked-events/{EVENT_ID}/release-source",
            headers={"X-MarketAI-Control-Key": "control-key"},
            json=payload,
        )
        long_actor = client.put(
            f"/api/v1/tracked-events/{EVENT_ID}/release-source",
            headers={
                "X-MarketAI-Control-Key": "control-key",
                "X-MarketAI-Actor": "a" * 201,
            },
            json=payload,
        )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(missing_actor.status_code, 422)
        self.assertEqual(long_actor.status_code, 422)
        self.assertEqual(tracked_repo.requested_event_ids, [])

    def test_put_malformed_uuid_and_missing_event_fail_before_source_write(self) -> None:
        client, tracked_repo, source_repo = _client(
            event=None, state=OfficialReleaseSourceState(source=None, version=0)
        )
        headers = {
            "X-MarketAI-Control-Key": "control-key",
            "X-MarketAI-Actor": "actor",
        }
        payload = {
            "source_kind": "results_page",
            "source_url": "https://example.com/results",
            "expected_version": 0,
        }

        malformed = client.put(
            "/api/v1/tracked-events/not-a-uuid/release-source",
            headers=headers,
            json=payload,
        )
        missing = client.put(
            f"/api/v1/tracked-events/{EVENT_ID}/release-source",
            headers=headers,
            json=payload,
        )

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(tracked_repo.requested_event_ids, [EVENT_ID])
        self.assertIsNone(source_repo.set_call)

    def test_put_maps_conflict_invalid_input_and_repository_failure(self) -> None:
        headers = {
            "X-MarketAI-Control-Key": "control-key",
            "X-MarketAI-Actor": "actor",
        }
        valid_payload = {
            "source_kind": "direct_url",
            "source_url": "https://example.com/results.pdf",
            "expected_version": 1,
        }
        expected_statuses = (
            (OfficialReleaseSourceVersionConflict(), valid_payload, 409),
            (None, {**valid_payload, "source_url": "http://example.com/results"}, 422),
            (RuntimeError("database unavailable"), valid_payload, 503),
        )
        for error, payload, expected_status in expected_statuses:
            with self.subTest(expected_status=expected_status):
                client, _, _ = _client(
                    event=_event(),
                    state=OfficialReleaseSourceState(source=None, version=0),
                    set_error=error,
                )
                response = client.put(
                    f"/api/v1/tracked-events/{EVENT_ID}/release-source",
                    headers=headers,
                    json=payload,
                )
                self.assertEqual(response.status_code, expected_status)

    def test_reads_source_through_canonical_tracked_identity(self) -> None:
        event = _event()
        source = OfficialReleaseSource(
            event_id=f"tracked:{EVENT_ID}",
            source_kind="direct_url",
            source_url="https://example.com/results.pdf",
            source_title="Q2 results",
            version=2,
        )
        client, tracked_repo, source_repo = _client(
            event=event,
            state=OfficialReleaseSourceState(source=source, version=2),
        )

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/release-source",
            headers={"X-MarketAI-Key": "read-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(tracked_repo.requested_event_ids, [EVENT_ID])
        self.assertEqual(source_repo.requested_event_id, f"tracked:{EVENT_ID}")
        self.assertEqual(response.json()["event_id"], EVENT_ID)
        self.assertEqual(response.json()["release_event_id"], f"tracked:{EVENT_ID}")
        self.assertEqual(response.json()["source_url"], "https://example.com/results.pdf")

    def test_requires_read_auth(self) -> None:
        client, _, _ = _client(
            event=_event(),
            state=OfficialReleaseSourceState(source=None, version=0),
        )

        response = client.get(f"/api/v1/tracked-events/{EVENT_ID}/release-source")

        self.assertEqual(response.status_code, 401)

    def test_malformed_tracked_event_id_is_400_without_repository_access(self) -> None:
        client, tracked_repo, source_repo = _client(
            event=_event(),
            state=OfficialReleaseSourceState(source=None, version=0),
        )

        response = client.get(
            "/api/v1/tracked-events/not-a-uuid/release-source",
            headers={"X-MarketAI-Key": "read-key"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(tracked_repo.requested_event_ids, [])
        self.assertIsNone(source_repo.requested_event_id)

    def test_missing_tracked_event_is_404(self) -> None:
        client, _, _ = _client(
            event=None,
            state=OfficialReleaseSourceState(source=None, version=0),
        )

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/release-source",
            headers={"X-MarketAI-Key": "read-key"},
        )

        self.assertEqual(response.status_code, 404)

    def test_identity_mismatch_fails_closed(self) -> None:
        source = OfficialReleaseSource(
            event_id="tracked:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            source_kind="results_page",
            source_url="https://example.com/investors",
            version=1,
        )
        client, _, _ = _client(
            event=_event(),
            state=OfficialReleaseSourceState(source=source, version=1),
        )

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/release-source",
            headers={"X-MarketAI-Key": "read-key"},
        )

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
