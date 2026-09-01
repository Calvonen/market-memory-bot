from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from trading_system.official_release_source_repository import OfficialReleaseSourceState
from trading_system.tracked_event_release_source_api import (
    build_tracked_event_release_source_router,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


TRACKED_ID = "11111111-1111-1111-1111-111111111111"
CALENDAR_ID = "22222222-2222-2222-2222-222222222222"


def _event(*, status: TrackedEventStatus, updated_at: datetime | None) -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id=TRACKED_ID,
        tracked_instrument_id="33333333-3333-3333-3333-333333333333",
        calendar_event_id=CALENDAR_ID,
        company_name="Example Oyj",
        instrument="EXAMPLE.HE",
        market="Helsinki",
        source="calendar",
        external_key="example-q2",
        kind="earnings",
        title="Q2 results",
        event_at=datetime(2026, 8, 29, 6, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=status,
        updated_at=updated_at,
    )


class _TrackedRepo:
    def __init__(self, event: PersistentTrackedEvent | None) -> None:
        self.event = event
        self.batch_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def get(self, event_id: str) -> PersistentTrackedEvent | None:
        return self.event if self.event and self.event.event_id == event_id else None

    def get_by_occurrences(
        self,
        *,
        event_ids: tuple[str, ...] = (),
        calendar_event_ids: tuple[str, ...] = (),
    ) -> tuple[PersistentTrackedEvent, ...]:
        self.batch_calls.append((event_ids, calendar_event_ids))
        if self.event is None:
            return ()
        if self.event.event_id in event_ids or self.event.calendar_event_id in calendar_event_ids:
            return (self.event,)
        return ()


class _SourceRepo:
    def get_state(self, event_id: str) -> OfficialReleaseSourceState:
        return OfficialReleaseSourceState(source=None, version=0)

    def set(self, source, *, expected_version: int, actor: str):  # pragma: no cover
        raise AssertionError("not used")


def _client(event: PersistentTrackedEvent | None) -> tuple[TestClient, _TrackedRepo]:
    tracked_repo = _TrackedRepo(event)

    def require_read(value: str | None) -> None:
        if value != "read-key":
            raise HTTPException(status_code=401, detail="Invalid read key")

    app = FastAPI()
    app.include_router(
        build_tracked_event_release_source_router(
            require_read=require_read,
            require_control=lambda _: None,
            get_tracked_event_repository=lambda: tracked_repo,
            get_official_release_source_repository=lambda: _SourceRepo(),
        )
    )
    return TestClient(app), tracked_repo


class TrackedEventActivityApiTests(unittest.TestCase):
    def test_batch_reads_tracked_and_calendar_occurrences_through_repository(self) -> None:
        client, repo = _client(
            _event(status=TrackedEventStatus.MONITORING, updated_at=datetime.now(UTC))
        )

        response = client.get(
            "/api/v1/tracked-events/activity",
            params={
                "occurrence_ids": f"tracked:{TRACKED_ID},calendar:{CALENDAR_ID}"
            },
            headers={"X-MarketAI-Key": "read-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repo.batch_calls, [((TRACKED_ID,), (CALENDAR_ID,))])
        self.assertEqual(
            response.json()["items"],
            [
                {
                    "occurrence_id": f"tracked:{TRACKED_ID}",
                    "exists": True,
                    "active": True,
                },
                {
                    "occurrence_id": f"calendar:{CALENDAR_ID}",
                    "exists": True,
                    "active": True,
                },
            ],
        )

    def test_batch_canonicalizes_dashless_uuid_but_preserves_response_key(self) -> None:
        client, repo = _client(
            _event(status=TrackedEventStatus.MONITORING, updated_at=datetime.now(UTC))
        )
        requested_id = TRACKED_ID.replace("-", "")

        response = client.get(
            "/api/v1/tracked-events/activity",
            params={"occurrence_ids": f"tracked:{requested_id}"},
            headers={"X-MarketAI-Key": "read-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repo.batch_calls, [((TRACKED_ID,), ())])
        self.assertEqual(
            response.json()["items"],
            [
                {
                    "occurrence_id": f"tracked:{requested_id}",
                    "exists": True,
                    "active": True,
                }
            ],
        )

    def test_old_terminal_row_is_inactive(self) -> None:
        client, _ = _client(
            _event(
                status=TrackedEventStatus.COMPLETED,
                updated_at=datetime.now(UTC) - timedelta(hours=25),
            )
        )

        response = client.get(
            "/api/v1/tracked-events/activity",
            params={"occurrence_ids": f"calendar:{CALENDAR_ID}"},
            headers={"X-MarketAI-Key": "read-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["items"][0]["active"])


if __name__ == "__main__":
    unittest.main()
