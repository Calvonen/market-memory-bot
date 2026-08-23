from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    SupabaseTrackedEventRepository,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


class _TrackedEventRepository:
    def __init__(self, events=(), error: str | None = None) -> None:
        self.events = tuple(events)
        self.error = error
        self.limits: list[int] = []

    def list_recent(self, *, limit: int = 20):
        self.limits.append(limit)
        if self.error is not None:
            raise RuntimeError(self.error)
        return self.events[:limit]


def _event() -> PersistentTrackedEvent:
    event_at = datetime(2026, 8, 23, 23, 30, tzinfo=UTC)
    return PersistentTrackedEvent(
        event_id="352e828c-88f8-4d1b-8ee7-effb11436fe4",
        tracked_instrument_id="tracked-nhf",
        calendar_event_id=None,
        company_name="nib holdings limited",
        instrument="NHF.ASX",
        market="Sydney",
        source="manual",
        external_key="nhf-fy26-2026-08-24",
        kind="earnings",
        title="FY26 results",
        event_at=event_at,
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=TrackedEventStatus.TRACKED,
        resolved_etoro_instrument_id=3343,
        resolved_etoro_symbol="NHF.ASX",
        resolved_etoro_display_name="nib holdings limited",
        resolution_armed_at=datetime(2026, 8, 23, 14, 0, tzinfo=UTC),
        resolution_armed_by="tracked-event-preflight",
        reference_price=Decimal("6.24"),
        reference_captured_at=datetime(2026, 8, 23, 23, 29, 45, tzinfo=UTC),
        reference_kind="etoro_last_execution_pre_event_snapshot",
        reaction_anchor_at=None,
        last_error=None,
        updated_at=datetime(2026, 8, 23, 23, 29, 45, tzinfo=UTC),
    )


class TrackedEventReadApiTests(unittest.TestCase):
    def test_requires_existing_read_key(self):
        repository = _TrackedEventRepository((_event(),))
        client = TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

        response = client.get("/api/v1/tracked-events")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(repository.limits, [])

    def test_returns_observation_only_runtime_state(self):
        repository = _TrackedEventRepository((_event(),))
        client = TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

        response = client.get(
            "/api/v1/tracked-events?limit=7",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repository.limits, [7])
        self.assertEqual(
            response.json(),
            [
                {
                    "event_id": "352e828c-88f8-4d1b-8ee7-effb11436fe4",
                    "tracked_instrument_id": "tracked-nhf",
                    "calendar_event_id": None,
                    "company_name": "nib holdings limited",
                    "instrument": "NHF.ASX",
                    "market": "Sydney",
                    "source": "manual",
                    "external_key": "nhf-fy26-2026-08-24",
                    "kind": "earnings",
                    "title": "FY26 results",
                    "event_at": "2026-08-23T23:30:00+00:00",
                    "event_time_status": "estimated",
                    "status": "tracked",
                    "resolved_etoro_instrument_id": 3343,
                    "resolved_etoro_symbol": "NHF.ASX",
                    "resolved_etoro_display_name": "nib holdings limited",
                    "resolution_armed_at": "2026-08-23T14:00:00+00:00",
                    "resolution_armed_by": "tracked-event-preflight",
                    "reference_price": "6.24",
                    "reference_captured_at": "2026-08-23T23:29:45+00:00",
                    "reference_kind": "etoro_last_execution_pre_event_snapshot",
                    "reaction_anchor_at": None,
                    "started_at": None,
                    "completed_at": None,
                    "last_error": None,
                    "updated_at": "2026-08-23T23:29:45+00:00",
                }
            ],
        )

    def test_repository_failure_is_503(self):
        repository = _TrackedEventRepository(error="tracked-event read failed")
        client = TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

        response = client.get(
            "/api/v1/tracked-events",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "tracked-event read failed")


class _Response:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.calls = []

    def select(self, value):
        self.calls.append(("select", value))
        return self

    def order(self, value, **kwargs):
        self.calls.append(("order", value, kwargs))
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        return self

    def execute(self):
        self.calls.append(("execute",))
        return _Response(self.rows)


class _Client:
    def __init__(self, rows) -> None:
        self.table_query = _Table(rows)
        self.table_names = []

    def table(self, name):
        self.table_names.append(name)
        return self.table_query


class TrackedEventRecentRepositoryTests(unittest.TestCase):
    def test_list_recent_is_bounded_and_newest_first(self):
        event = _event()
        client = _Client(
            [
                {
                    "id": event.event_id,
                    "tracked_instrument_id": event.tracked_instrument_id,
                    "calendar_event_id": None,
                    "company_name": event.company_name,
                    "instrument": event.instrument,
                    "market": event.market,
                    "source": event.source,
                    "external_key": event.external_key,
                    "kind": event.kind,
                    "title": event.title,
                    "event_at": event.event_at.isoformat(),
                    "event_time_status": event.event_time_status.value,
                    "status": event.status.value,
                }
            ]
        )
        repository = SupabaseTrackedEventRepository(client)

        rows = repository.list_recent(limit=7)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].event_id, event.event_id)
        self.assertEqual(client.table_names, ["tracked_market_events"])
        self.assertEqual(
            client.table_query.calls,
            [
                ("select", "*"),
                ("order", "event_at", {"desc": True}),
                ("limit", 7),
                ("execute",),
            ],
        )

    def test_list_recent_rejects_unbounded_limits(self):
        repository = SupabaseTrackedEventRepository(_Client([]))

        with self.assertRaises(ValueError):
            repository.list_recent(limit=0)
        with self.assertRaises(ValueError):
            repository.list_recent(limit=101)


if __name__ == "__main__":
    unittest.main()
