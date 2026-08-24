from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


EVENT_ID = "352e828c-88f8-4d1b-8ee7-effb11436fe4"


class _TrackedEventRepository:
    def __init__(
        self,
        *,
        event: PersistentTrackedEvent | None,
        reactions: tuple[TrackedEventReactionRecord, ...] = (),
        get_error: Exception | None = None,
        reactions_error: Exception | None = None,
    ) -> None:
        self.event = event
        self.reactions = reactions
        self.get_error = get_error
        self.reactions_error = reactions_error
        self.get_calls: list[str] = []
        self.reaction_calls: list[str] = []

    def get(self, event_id: str) -> PersistentTrackedEvent | None:
        self.get_calls.append(event_id)
        if self.get_error is not None:
            raise self.get_error
        return self.event if event_id.replace("-", "") == EVENT_ID.replace("-", "") else None

    def list_reactions(self, event_id: str) -> tuple[TrackedEventReactionRecord, ...]:
        self.reaction_calls.append(event_id)
        if self.reactions_error is not None:
            raise self.reactions_error
        return self.reactions


def _event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id=EVENT_ID,
        tracked_instrument_id="tracked-nhf",
        calendar_event_id=None,
        company_name="nib holdings limited",
        instrument="NHF.ASX",
        market="Sydney",
        source="manual",
        external_key="nhf-fy26-2026-08-24",
        kind="earnings",
        title="FY26 results",
        event_at=datetime(2026, 8, 23, 23, 30, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=TrackedEventStatus.MONITORING,
    )


def _reaction(
    *,
    candle_start: datetime | None = None,
    observed_at: datetime | None = None,
) -> TrackedEventReactionRecord:
    return TrackedEventReactionRecord(
        tracked_market_event_id=EVENT_ID,
        interval_minutes=15,
        candle_start=candle_start or datetime(2026, 8, 24, 0, 15, tzinfo=UTC),
        reference_price=Decimal("7.123456"),
        close_price=Decimal("7.987654"),
        return_pct=Decimal("12.3456789"),
        direction="up",
        evolution="extending",
        observed_at=observed_at or datetime(2026, 8, 24, 0, 30, tzinfo=UTC),
    )


class TrackedEventLatestReactionReadApiTests(unittest.TestCase):
    def _client(self, repository: _TrackedEventRepository) -> TestClient:
        return TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

    def test_requires_read_key_before_repository_access(self):
        repository = _TrackedEventRepository(event=_event(), reactions=(_reaction(),))
        client = self._client(repository)

        response = client.get(f"/api/v1/tracked-events/{EVENT_ID}/latest-reaction")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(repository.get_calls, [])
        self.assertEqual(repository.reaction_calls, [])

    def test_rejects_malformed_event_id_before_repository_access(self):
        repository = _TrackedEventRepository(event=_event())
        client = self._client(repository)

        response = client.get(
            "/api/v1/tracked-events/not-a-uuid/latest-reaction",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "event_id must be a valid UUID")
        self.assertEqual(repository.get_calls, [])
        self.assertEqual(repository.reaction_calls, [])

    def test_returns_exact_latest_persisted_reaction_values(self):
        repository = _TrackedEventRepository(event=_event(), reactions=(_reaction(),))
        client = self._client(repository)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/latest-reaction",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repository.get_calls, [EVENT_ID])
        self.assertEqual(repository.reaction_calls, [EVENT_ID])
        self.assertEqual(
            response.json(),
            {
                "event_id": EVENT_ID,
                "latest_reaction": {
                    "interval_minutes": 15,
                    "candle_start": "2026-08-24T00:15:00+00:00",
                    "reference_price": "7.123456",
                    "close_price": "7.987654",
                    "return_pct": "12.3456789",
                    "direction": "up",
                    "evolution": "extending",
                    "observed_at": "2026-08-24T00:30:00+00:00",
                },
            },
        )

    def test_compact_uuid_returns_and_reads_with_canonical_event_id(self):
        compact_event_id = EVENT_ID.replace("-", "")
        repository = _TrackedEventRepository(event=_event(), reactions=(_reaction(),))
        client = self._client(repository)

        response = client.get(
            f"/api/v1/tracked-events/{compact_event_id}/latest-reaction",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repository.get_calls, [compact_event_id])
        self.assertEqual(repository.reaction_calls, [EVENT_ID])
        self.assertEqual(response.json()["event_id"], EVENT_ID)

    def test_existing_event_without_reactions_returns_null(self):
        repository = _TrackedEventRepository(event=_event())
        client = self._client(repository)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/latest-reaction",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"event_id": EVENT_ID, "latest_reaction": None})
        self.assertEqual(repository.reaction_calls, [EVENT_ID])

    def test_missing_event_is_404_without_reaction_query(self):
        repository = _TrackedEventRepository(event=None)
        client = self._client(repository)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/latest-reaction",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Tracked event not found")
        self.assertEqual(repository.reaction_calls, [])

    def test_malformed_reaction_timestamp_fails_closed_as_503(self):
        repository = _TrackedEventRepository(
            event=_event(),
            reactions=(
                _reaction(candle_start=datetime(2026, 8, 24, 0, 15)),
            ),
        )
        client = self._client(repository)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/latest-reaction",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "candle_start must be timezone-aware")

    def test_repository_failures_are_503(self):
        repository = _TrackedEventRepository(
            event=_event(),
            reactions_error=RuntimeError("reaction read failed"),
        )
        client = self._client(repository)

        response = client.get(
            f"/api/v1/tracked-events/{EVENT_ID}/latest-reaction",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "reaction read failed")


if __name__ == "__main__":
    unittest.main()
