from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceEventNotFound,
    OfficialReleaseSourceState,
    OfficialReleaseSourceVersionConflict,
    SupabaseOfficialReleaseSourceRepository,
)

EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"
READ_KEY = "read-key"
CONTROL_KEY = "control-key"
ADMIN_TOKEN = "admin-token"
ACTOR = "marko"


class _EventRepository:
    def __init__(self, exists: bool = True, error: Exception | None = None):
        self.exists = exists
        self.error = error

    def get(self, event_id: str):
        if self.error is not None:
            raise self.error
        return object() if self.exists and event_id == EVENT_ID else None


class _SourceRepository:
    def __init__(self):
        self.source = None
        self.version = 0
        self.state_calls = []
        self.set_calls = []
        self.clear_calls = []
        self.set_error = None
        self.read_error = None

    def get_state(self, event_id: str):
        self.state_calls.append(event_id)
        if self.read_error is not None:
            raise self.read_error
        return OfficialReleaseSourceState(self.source, self.version)

    def set(self, source: OfficialReleaseSource, *, expected_version: int, actor: str):
        self.set_calls.append((source, expected_version, actor))
        if self.set_error is not None:
            raise self.set_error
        if expected_version != self.version:
            raise OfficialReleaseSourceVersionConflict()
        self.version += 1
        self.source = OfficialReleaseSource(
            event_id=source.event_id,
            source_kind=source.source_kind,
            source_url=source.source_url,
            source_title=source.source_title,
            version=self.version,
        )
        return self.source

    def clear(self, event_id: str, *, expected_version: int, actor: str):
        self.clear_calls.append((event_id, expected_version, actor))
        if expected_version != self.version:
            raise OfficialReleaseSourceVersionConflict()
        self.version += 1
        self.source = None
        return self.version


def _client(source_repository=None, event_repository=None):
    return TestClient(
        create_app(
            repository=event_repository or _EventRepository(),
            official_release_source_repository=source_repository or _SourceRepository(),
            read_api_key=READ_KEY,
            control_api_key=CONTROL_KEY,
            admin_token=ADMIN_TOKEN,
        )
    )


class OfficialReleaseSourceControlApiTests(unittest.TestCase):
    def test_get_requires_read_auth_and_reads_state_once(self):
        source_repository = _SourceRepository()
        source_repository.version = 4
        client = _client(source_repository)
        unauthorized = client.get(f"/api/v1/events/{EVENT_ID}/official-release-source")
        self.assertEqual(unauthorized.status_code, 401)
        response = client.get(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Key": READ_KEY},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(source_repository.state_calls, [EVENT_ID])
        self.assertEqual(response.json()["active"], False)
        self.assertEqual(response.json()["version"], 4)

    def test_put_rejects_mobile_control_key_and_requires_admin_actor(self):
        source_repository = _SourceRepository()
        client = _client(source_repository)
        body = {
            "source_kind": "results_page",
            "source_url": "https://investor.example.com/results",
            "source_title": " Results ",
            "expected_version": 0,
        }
        control_only = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Control-Key": CONTROL_KEY, "X-MarketAI-Actor": ACTOR},
            json=body,
        )
        self.assertEqual(control_only.status_code, 401)
        missing_actor = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN},
            json=body,
        )
        self.assertEqual(missing_actor.status_code, 422)
        self.assertEqual(source_repository.set_calls, [])
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": "  marko  "},
            json=body,
        )
        self.assertEqual(response.status_code, 200)
        saved, expected, actor = source_repository.set_calls[-1]
        self.assertEqual((expected, actor, saved.source_title), (0, ACTOR, "Results"))

    def test_put_rejects_invalid_url_before_repository_write(self):
        source_repository = _SourceRepository()
        client = _client(source_repository)
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
            json={
                "source_kind": "direct_url",
                "source_url": "http://investor.example.com/results.pdf",
                "expected_version": 0,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(source_repository.set_calls, [])

    def test_put_version_conflict_is_409(self):
        source_repository = _SourceRepository()
        source_repository.version = 2
        client = _client(source_repository)
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
            json={
                "source_kind": "direct_url",
                "source_url": "https://investor.example.com/results.pdf",
                "expected_version": 1,
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_delete_requires_admin_actor_and_records_actor(self):
        source_repository = _SourceRepository()
        source_repository.version = 3
        source_repository.source = OfficialReleaseSource(
            EVENT_ID, "direct_url", "https://investor.example.com/results.pdf", version=3
        )
        client = _client(source_repository)
        control_only = client.delete(
            f"/api/v1/events/{EVENT_ID}/official-release-source?expected_version=3",
            headers={"X-MarketAI-Control-Key": CONTROL_KEY, "X-MarketAI-Actor": ACTOR},
        )
        self.assertEqual(control_only.status_code, 401)
        response = client.delete(
            f"/api/v1/events/{EVENT_ID}/official-release-source?expected_version=3",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(source_repository.clear_calls[-1], (EVENT_ID, 3, ACTOR))
        self.assertEqual(response.json()["version"], 4)

    def test_unknown_event_is_404_before_source_repository_access(self):
        source_repository = _SourceRepository()
        client = _client(source_repository, _EventRepository(exists=False))
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
            json={
                "source_kind": "results_page",
                "source_url": "https://investor.example.com/results",
                "expected_version": 0,
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(source_repository.set_calls, [])

    def test_source_read_client_exception_is_503(self):
        source_repository = _SourceRepository()
        source_repository.read_error = _ApiError("08006", "connection failure")
        client = _client(source_repository)
        response = client.get(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Key": READ_KEY},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Official release source read failed")

    def test_event_precheck_client_exception_is_503(self):
        client = _client(event_repository=_EventRepository(error=_ApiError("08006", "connection failure")))
        response = client.get(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Key": READ_KEY},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Event repository read failed")

    def test_repository_failure_is_503(self):
        source_repository = _SourceRepository()
        source_repository.set_error = RuntimeError("source backend unavailable")
        client = _client(source_repository)
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-Admin-Token": ADMIN_TOKEN, "X-MarketAI-Actor": ACTOR},
            json={
                "source_kind": "results_page",
                "source_url": "https://investor.example.com/results",
                "expected_version": 0,
            },
        )
        self.assertEqual(response.status_code, 503)


class _ApiError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _ErrorRpc:
    def __init__(self, error): self.error = error
    def execute(self): raise self.error


class _ErrorClient:
    def __init__(self, error): self.error = error
    def rpc(self, name, payload): return _ErrorRpc(self.error)


class OfficialReleaseSourceRepositoryErrorTranslationTests(unittest.TestCase):
    def _source(self):
        return OfficialReleaseSource(EVENT_ID, "direct_url", "https://investor.example.com/results.pdf")

    def test_set_translates_marked_sqlstate_version_conflict(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("40001", "version_conflict: expected 1, current 2"))
        )
        with self.assertRaises(OfficialReleaseSourceVersionConflict):
            repository.set(self._source(), expected_version=1, actor=ACTOR)

    def test_bare_serialization_failure_stays_service_failure(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("40001", "could not serialize access due to concurrent update"))
        )
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            repository.set(self._source(), expected_version=1, actor=ACTOR)

    def test_set_translates_missing_event(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("P0002", f"event_not_found: {EVENT_ID}"))
        )
        with self.assertRaises(OfficialReleaseSourceEventNotFound):
            repository.set(self._source(), expected_version=0, actor=ACTOR)

    def test_unknown_rpc_error_stays_service_failure(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("08006", "connection failure"))
        )
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            repository.set(self._source(), expected_version=0, actor=ACTOR)


if __name__ == "__main__":
    unittest.main()
