from __future__ import annotations

import unittest
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
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

    def get(self, event_id: str) -> PersistentTrackedEvent | None:
        return self.event if event_id == EVENT_ID else None


class _SourceRepo:
    def __init__(self, state: OfficialReleaseSourceState) -> None:
        self.state = state
        self.requested_event_id: str | None = None

    def get_state(self, event_id: str) -> OfficialReleaseSourceState:
        self.requested_event_id = event_id
        return self.state


def _client(
    *,
    event: PersistentTrackedEvent | None,
    state: OfficialReleaseSourceState,
    read_key: str = "read-key",
) -> tuple[TestClient, _SourceRepo]:
    tracked_repo = _TrackedRepo(event)
    source_repo = _SourceRepo(state)

    def require_read(value: str | None) -> None:
        if value != read_key:
            raise HTTPException(status_code=401, detail="Invalid or missing read API key")

    app = FastAPI()
    app.include_router(
        build_tracked_event_release_source_router(
            require_read=require_read,
            get_tracked_event_repository=lambda: tracked_repo,
            get_official_release_source_repository=lambda: source_repo,
        )
    )
    return TestClient(app), source_repo


class TrackedEventReleaseSourceApiTests(unittest.TestCase):
    def test_reads_source_through_canonical_tracked_identity(self) -> None:
        event = _event()
        source = OfficialReleaseSource(
            event_id=f"tracked:{EVENT_ID}",
            source_kind="direct_url",
            source_url="https://example.com/results.pdf",
            source_title="Q2 results",
            version=2,
        )
        client, source_repo = _client(
            event=event,
            state=OfficialReleaseSourceState(source=source, version=2),
        )

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/release-source",
            headers={"X-MarketAI-Key": "read-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(source_repo.requested_event_id, f"tracked:{EVENT_ID}")
        self.assertEqual(response.json()["event_id"], EVENT_ID)
        self.assertEqual(response.json()["release_event_id"], f"tracked:{EVENT_ID}")
        self.assertEqual(response.json()["source_url"], "https://example.com/results.pdf")

    def test_requires_read_auth(self) -> None:
        client, _ = _client(
            event=_event(),
            state=OfficialReleaseSourceState(source=None, version=0),
        )

        response = client.get(f"/api/v1/tracked-events/{EVENT_ID}/release-source")

        self.assertEqual(response.status_code, 401)

    def test_missing_tracked_event_is_404(self) -> None:
        client, _ = _client(
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
        client, _ = _client(
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
