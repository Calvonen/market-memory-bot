from __future__ import annotations

import unittest
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


SNAPSHOT = {
    "schema_version": 1,
    "monitor_hours": 8.0,
    "reference_lead_seconds": 30.0,
    "max_wait_for_market_hours": 72.0,
    "reaction_stages": [
        {"start_after_minutes": 0, "interval_minutes": 1},
        {"start_after_minutes": 30, "interval_minutes": 5},
        {"start_after_minutes": 150, "interval_minutes": 15},
    ],
}


def _event(snapshot):
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
        event_at=datetime(2026, 8, 23, 23, 30, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=TrackedEventStatus.TRACKED,
        tracking_config_snapshot=snapshot,
    )


class _Repository:
    def __init__(self, event):
        self.event = event

    def list_active(self, *, limit=20, now):
        return (self.event,)

    def list_history(self, *, limit=20, now):
        return (self.event,)


class TrackedEventConfigReadApiTests(unittest.TestCase):
    def test_snapshot_round_trips_for_active_and_history(self):
        client = TestClient(
            create_app(
                tracked_event_repository=_Repository(_event(SNAPSHOT)),
                read_api_key="read-secret",
            )
        )
        headers = {"X-MarketAI-Key": "read-secret"}

        active = client.get("/api/v1/tracked-events?view=active", headers=headers)
        history = client.get("/api/v1/tracked-events?view=history", headers=headers)

        self.assertEqual(active.status_code, 200)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(active.json()[0]["tracking_config_snapshot"], SNAPSHOT)
        self.assertEqual(history.json()[0]["tracking_config_snapshot"], SNAPSHOT)

    def test_legacy_null_snapshot_stays_null(self):
        client = TestClient(
            create_app(
                tracked_event_repository=_Repository(_event(None)),
                read_api_key="read-secret",
            )
        )

        response = client.get(
            "/api/v1/tracked-events",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()[0]["tracking_config_snapshot"])


if __name__ == "__main__":
    unittest.main()
