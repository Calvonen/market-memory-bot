from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceEventNotFound,
    OfficialReleaseSourceVersionConflict,
    SupabaseOfficialReleaseSourceRepository,
)


EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"
READ_KEY = "read-key"
CONTROL_KEY = "control-key"


class _EventRepository:
    def __init__(self, exists: bool = True):
        self.exists = exists

    def get(self, event_id: str):
        return object() if self.exists and event_id == EVENT_ID else None


class _SourceRepository:
    def __init__(self):
        self.source = None
        self.version = 0
        self.set_calls = []
        self.clear_calls = []
        self.set_error = None

    def get(self, event_id: str):
        return self.source

    def get_version(self, event_id: str):
        return self.version

    def set(self, source: OfficialReleaseSource, *, expected_version: int):
        self.set_calls.append((source, expected_version))
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

    def clear(self, event_id: str, *, expected_version: int):
        self.clear_calls.append((event_id, expected_version))
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
        )
    )


class OfficialReleaseSourceControlApiTests(unittest.TestCase):
    def test_get_requires_read_auth_and_returns_versioned_inactive_state(self):
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
        self.assertEqual(response.json(), {
            "event_id": EVENT_ID,
            "active": False,
            "version": 4,
            "source_kind": None,
            "source_url": None,
            "source_title": None,
        })

    def test_put_requires_control_auth_and_sets_canonical_source(self):
        source_repository = _SourceRepository()
        client = _client(source_repository)
        body = {
            "source_kind": "results_page",
            "source_url": "https://investor.example.com/results",
            "source_title": " Results ",
            "expected_version": 0,
        }
        unauthorized = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source", json=body
        )
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(source_repository.set_calls, [])
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Control-Key": CONTROL_KEY},
            json=body,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 1)
        self.assertTrue(response.json()["active"])
        saved, expected = source_repository.set_calls[-1]
        self.assertEqual(expected, 0)
        self.assertEqual(saved.source_title, "Results")

    def test_put_rejects_invalid_url_before_repository_write(self):
        source_repository = _SourceRepository()
        client = _client(source_repository)
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Control-Key": CONTROL_KEY},
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
            headers={"X-MarketAI-Control-Key": CONTROL_KEY},
            json={
                "source_kind": "direct_url",
                "source_url": "https://investor.example.com/results.pdf",
                "expected_version": 1,
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_delete_clears_to_versioned_tombstone(self):
        source_repository = _SourceRepository()
        source_repository.version = 3
        source_repository.source = OfficialReleaseSource(
            EVENT_ID,
            "direct_url",
            "https://investor.example.com/results.pdf",
            version=3,
        )
        client = _client(source_repository)
        response = client.delete(
            f"/api/v1/events/{EVENT_ID}/official-release-source?expected_version=3",
            headers={"X-MarketAI-Control-Key": CONTROL_KEY},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "event_id": EVENT_ID,
            "active": False,
            "version": 4,
            "source_kind": None,
            "source_url": None,
            "source_title": None,
        })

    def test_unknown_event_is_404_before_source_repository_access(self):
        source_repository = _SourceRepository()
        client = _client(source_repository, _EventRepository(exists=False))
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Control-Key": CONTROL_KEY},
            json={
                "source_kind": "results_page",
                "source_url": "https://investor.example.com/results",
                "expected_version": 0,
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(source_repository.set_calls, [])

    def test_repository_failure_is_503(self):
        source_repository = _SourceRepository()
        source_repository.set_error = RuntimeError("source backend unavailable")
        client = _client(source_repository)
        response = client.put(
            f"/api/v1/events/{EVENT_ID}/official-release-source",
            headers={"X-MarketAI-Control-Key": CONTROL_KEY},
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
    def __init__(self, error):
        self.error = error

    def execute(self):
        raise self.error


class _ErrorClient:
    def __init__(self, error):
        self.error = error

    def rpc(self, name, payload):
        return _ErrorRpc(self.error)


class OfficialReleaseSourceRepositoryErrorTranslationTests(unittest.TestCase):
    def test_set_translates_sqlstate_version_conflict(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("40001", "version_conflict: expected 1, current 2"))
        )
        source = OfficialReleaseSource(
            EVENT_ID, "direct_url", "https://investor.example.com/results.pdf"
        )
        with self.assertRaises(OfficialReleaseSourceVersionConflict):
            repository.set(source, expected_version=1)

    def test_set_translates_missing_event(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("P0002", f"event_not_found: {EVENT_ID}"))
        )
        source = OfficialReleaseSource(
            EVENT_ID, "direct_url", "https://investor.example.com/results.pdf"
        )
        with self.assertRaises(OfficialReleaseSourceEventNotFound):
            repository.set(source, expected_version=0)

    def test_unknown_rpc_error_stays_service_failure(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("08006", "connection failure"))
        )
        source = OfficialReleaseSource(
            EVENT_ID, "direct_url", "https://investor.example.com/results.pdf"
        )
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            repository.set(source, expected_version=0)


if __name__ == "__main__":
    unittest.main()
