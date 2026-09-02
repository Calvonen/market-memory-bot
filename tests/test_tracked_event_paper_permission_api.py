from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from trading_system.models import EventExpectation, TradingMode
from trading_system.official_release_source_repository import OfficialReleaseSourceState
from trading_system.tracked_event_release_source_api import build_tracked_event_release_source_router
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)
from trading_system.trading_task import CanonicalTradingTask, TradingTaskState


EVENT_ID = "11111111-1111-1111-1111-111111111111"
SOURCE_EVENT_ID = f"tracked:{EVENT_ID}"


def _event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id=EVENT_ID,
        tracked_instrument_id="22222222-2222-2222-2222-222222222222",
        calendar_event_id=None,
        company_name="Daktronics Inc",
        instrument="DAKT",
        market="NASDAQ",
        source="manual",
        external_key="dakt-q1",
        kind="earnings",
        title="Q1 results",
        event_at=datetime(2026, 9, 2, 13, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=TrackedEventStatus.TRACKED,
    )


def _expectation(version: int = 2) -> EventExpectation:
    return EventExpectation(
        event_id=SOURCE_EVENT_ID,
        instrument="DAKT",
        event_name="Q1 results",
        scheduled_date=date(2026, 9, 2),
        consensus={},
        important_kpis=(),
        bull_case=(),
        base_case=(),
        bear_case=(),
        triggers={},
        invalidation_conditions=(),
        source_name=None,
        source_url=None,
        source_as_of=None,
        version=version,
        updated_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
    )


class _TrackedRepo:
    def get(self, event_id: str) -> PersistentTrackedEvent | None:
        return _event() if event_id == EVENT_ID else None


class _SourceRepo:
    def get_state(self, event_id: str) -> OfficialReleaseSourceState:
        return OfficialReleaseSourceState(source=None, version=0)


class _ExpectationRepo:
    def __init__(self, version: int = 2) -> None:
        self.version = version

    def get(self, event_id: str) -> EventExpectation | None:
        return _expectation(self.version) if event_id == SOURCE_EVENT_ID else None


class _TaskRepo:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self.row = row
        self.create_calls = 0
        self.approve_calls = 0
        self.cancel_calls = 0

    def get_active_row_for_event_mode(self, *, tracked_event_id: str, mode: TradingMode):
        self._assert_identity(tracked_event_id, mode)
        return dict(self.row) if self.row is not None else None

    def create_pending(self, *, tracked_event_id: str, source_event_id: str, instrument: str, mode: TradingMode, actor: str):
        self._assert_identity(tracked_event_id, mode)
        assert source_event_id == SOURCE_EVENT_ID
        assert instrument == "DAKT"
        self.create_calls += 1
        now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
        self.row = {
            "id": "33333333-3333-3333-3333-333333333333",
            "tracked_event_id": EVENT_ID,
            "source_event_id": SOURCE_EVENT_ID,
            "instrument": "DAKT",
            "mode": "PAPER",
            "state": "pending",
            "created_by": actor,
            "created_at": now.isoformat(),
            "approved_by": None,
            "approved_at": None,
            "approved_expectation_version": None,
        }
        return self.get(str(self.row["id"]))

    def approve(self, *, task_id: str, actor: str):
        self.approve_calls += 1
        assert self.row is not None and task_id == self.row["id"]
        self.row.update(
            state="approved",
            approved_by=actor,
            approved_at=datetime(2026, 9, 2, 12, 1, tzinfo=UTC).isoformat(),
            approved_expectation_version=2,
        )
        return self.get(task_id)

    def cancel(self, *, task_id: str, actor: str):
        self.cancel_calls += 1
        assert self.row is not None and task_id == self.row["id"]
        old = self.get(task_id)
        self.row = None
        return old

    def get(self, task_id: str):
        if self.row is None or task_id != self.row["id"]:
            return None
        row = self.row
        return CanonicalTradingTask(
            task_id=str(row["id"]),
            tracked_event_id=EVENT_ID,
            source_event_id=SOURCE_EVENT_ID,
            instrument="DAKT",
            mode=TradingMode.PAPER,
            state=TradingTaskState(str(row["state"])),
            created_by=str(row["created_by"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            approved_by=str(row["approved_by"]) if row.get("approved_by") else None,
            approved_at=datetime.fromisoformat(str(row["approved_at"])) if row.get("approved_at") else None,
        )

    @staticmethod
    def _assert_identity(tracked_event_id: str, mode: TradingMode) -> None:
        assert tracked_event_id == EVENT_ID
        assert mode is TradingMode.PAPER


def _approved_row(version: int = 2) -> dict[str, object]:
    return {
        "id": "33333333-3333-3333-3333-333333333333",
        "tracked_event_id": EVENT_ID,
        "source_event_id": SOURCE_EVENT_ID,
        "instrument": "DAKT",
        "mode": "PAPER",
        "state": "approved",
        "created_by": "Marko",
        "created_at": datetime(2026, 9, 2, 12, 0, tzinfo=UTC).isoformat(),
        "approved_by": "Marko",
        "approved_at": datetime(2026, 9, 2, 12, 1, tzinfo=UTC).isoformat(),
        "approved_expectation_version": version,
    }


def _client(task_repo: _TaskRepo, expectation_repo: _ExpectationRepo | None = None) -> TestClient:
    def require_read(value: str | None) -> None:
        if value != "read-key":
            raise HTTPException(status_code=401, detail="bad read key")

    def require_control(value: str | None) -> None:
        if value != "control-key":
            raise HTTPException(status_code=401, detail="bad control key")

    app = FastAPI()
    app.include_router(
        build_tracked_event_release_source_router(
            require_read=require_read,
            require_control=require_control,
            get_tracked_event_repository=lambda: _TrackedRepo(),
            get_official_release_source_repository=lambda: _SourceRepo(),
            get_trading_task_repository=lambda: task_repo,
            get_event_expectation_repository=lambda: expectation_repo or _ExpectationRepo(),
        )
    )
    return TestClient(app)


class TrackedEventPaperPermissionApiTests(unittest.TestCase):
    def test_get_reports_missing_permission_without_creating_execution_authority(self) -> None:
        task_repo = _TaskRepo()
        response = _client(task_repo).get(
            f"/api/v1/tracked-events/{EVENT_ID}/paper-permission",
            headers={"X-MarketAI-Key": "read-key"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["state"], "not_created")
        self.assertFalse(payload["approval_current"])
        self.assertEqual(payload["mode"], "PAPER")
        self.assertEqual(payload["current_expectation_version"], 2)
        self.assertEqual(task_repo.create_calls, 0)

    def test_approve_creates_and_approves_exactly_one_paper_task(self) -> None:
        task_repo = _TaskRepo()
        response = _client(task_repo).post(
            f"/api/v1/tracked-events/{EVENT_ID}/paper-permission/approve",
            headers={
                "X-MarketAI-Control-Key": "control-key",
                "X-MarketAI-Actor": " Marko ",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["state"], "approved")
        self.assertTrue(payload["approval_current"])
        self.assertEqual(payload["approved_expectation_version"], 2)
        self.assertEqual((task_repo.create_calls, task_repo.approve_calls), (1, 1))

    def test_repeated_current_approval_is_idempotent(self) -> None:
        task_repo = _TaskRepo(_approved_row())
        response = _client(task_repo).post(
            f"/api/v1/tracked-events/{EVENT_ID}/paper-permission/approve",
            headers={
                "X-MarketAI-Control-Key": "control-key",
                "X-MarketAI-Actor": "Marko",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["approval_current"])
        self.assertEqual((task_repo.create_calls, task_repo.approve_calls, task_repo.cancel_calls), (0, 0, 0))

    def test_stale_approval_is_replaced_by_explicit_current_version_approval(self) -> None:
        task_repo = _TaskRepo(_approved_row(version=1))
        response = _client(task_repo).post(
            f"/api/v1/tracked-events/{EVENT_ID}/paper-permission/approve",
            headers={
                "X-MarketAI-Control-Key": "control-key",
                "X-MarketAI-Actor": "Marko",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["approval_current"])
        self.assertEqual((task_repo.cancel_calls, task_repo.create_calls, task_repo.approve_calls), (1, 1, 1))

    def test_approve_requires_control_key_and_actor_before_mutation(self) -> None:
        task_repo = _TaskRepo()
        client = _client(task_repo)
        no_key = client.post(
            f"/api/v1/tracked-events/{EVENT_ID}/paper-permission/approve",
            headers={"X-MarketAI-Actor": "Marko"},
        )
        no_actor = client.post(
            f"/api/v1/tracked-events/{EVENT_ID}/paper-permission/approve",
            headers={"X-MarketAI-Control-Key": "control-key"},
        )
        self.assertEqual(no_key.status_code, 401)
        self.assertEqual(no_actor.status_code, 422)
        self.assertEqual((task_repo.create_calls, task_repo.approve_calls, task_repo.cancel_calls), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
