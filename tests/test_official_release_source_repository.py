from __future__ import annotations

import unittest
from types import SimpleNamespace

from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    SupabaseOfficialReleaseSourceRepository,
)


class _Query:
    def __init__(self, *, response_rows=None):
        self.response_rows = [] if response_rows is None else response_rows
        self.calls = []

    def select(self, value):
        self.calls.append(("select", value))
        return self

    def eq(self, key, value):
        self.calls.append(("eq", key, value))
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        return self

    def execute(self):
        self.calls.append(("execute",))
        return SimpleNamespace(data=self.response_rows)


class _RpcQuery:
    def __init__(self, response_rows):
        self.response_rows = response_rows
        self.calls = []

    def execute(self):
        self.calls.append(("execute",))
        return SimpleNamespace(data=self.response_rows)


class _Client:
    def __init__(self, *, response_rows=None, rpc_rows=None, rpc_rows_by_name=None):
        self.response_rows = response_rows
        self.rpc_rows = [] if rpc_rows is None else rpc_rows
        self.rpc_rows_by_name = rpc_rows_by_name or {}
        self.queries = []
        self.rpc_calls = []

    def table(self, name):
        query = _Query(response_rows=self.response_rows)
        query.calls.append(("table", name))
        self.queries.append(query)
        return query

    def rpc(self, name, payload):
        self.rpc_calls.append((name, payload))
        rows = self.rpc_rows_by_name.get(name, self.rpc_rows)
        return _RpcQuery(rows)


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


class OfficialReleaseSourceRepositoryTests(unittest.TestCase):
    EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"

    def test_source_requires_canonical_event_kind_and_https_url(self):
        source = OfficialReleaseSource(
            event_id=f"  {self.EVENT_ID}  ",
            source_kind="direct_url",
            source_url="https://investor.example.com/results.pdf",
            source_title=" FY2026 results ",
        )
        self.assertEqual(source.event_id, self.EVENT_ID)
        self.assertEqual(source.source_title, "FY2026 results")

        with self.assertRaisesRegex(ValueError, "event_id is required"):
            OfficialReleaseSource("", "direct_url", "https://example.com/results")
        with self.assertRaisesRegex(ValueError, "direct_url or results_page"):
            OfficialReleaseSource(self.EVENT_ID, "auto_discovery", "https://example.com/results")
        with self.assertRaisesRegex(ValueError, "absolute HTTPS URL"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "http://example.com/results")
        with self.assertRaisesRegex(ValueError, "absolute HTTPS URL"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://example.com/a b")
        with self.assertRaisesRegex(ValueError, "no credentials"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://user:pass@example.com/results")
        with self.assertRaisesRegex(ValueError, "no credentials"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://@example.com/release")
        with self.assertRaisesRegex(ValueError, "no credentials"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://:@example.com/release")
        with self.assertRaisesRegex(ValueError, "valid host"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://:")
        with self.assertRaisesRegex(ValueError, "valid host"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://example..com/release")
        with self.assertRaisesRegex(ValueError, "valid host"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://-example.com/release")
        with self.assertRaisesRegex(ValueError, "valid host"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://./release")
        with self.assertRaisesRegex(ValueError, "valid host"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://example.com../release")
        rooted = OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://example.com./release")
        self.assertEqual(rooted.source_url, "https://example.com./release")
        uppercase_scheme = OfficialReleaseSource(self.EVENT_ID, "direct_url", "HTTPS://example.com/release")
        self.assertEqual(uppercase_scheme.source_url, "HTTPS://example.com/release")
        with self.assertRaisesRegex(ValueError, "valid port"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://example.com:99999/results")
        with self.assertRaisesRegex(ValueError, "version must be positive"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://example.com/results", version=0)

    def _state_client(self, *, active, version, kind=None, url=None, title=None):
        return _Client(rpc_rows_by_name={
            "get_audited_official_release_source_state": [{
                "out_event_id": self.EVENT_ID,
                "out_source_kind": kind,
                "out_source_url": url,
                "out_source_title": title,
                "out_is_active": active,
                "out_version": version,
            }]
        })

    def test_get_returns_validated_canonical_source(self):
        client = self._state_client(active=True, version=3, kind="results_page", url="https://investor.example.com/results", title="Investor results")
        repository = SupabaseOfficialReleaseSourceRepository(client)
        source = repository.get(self.EVENT_ID)
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.source_kind, "results_page")
        self.assertEqual(source.version, 3)
        self.assertEqual(client.rpc_calls, [("get_audited_official_release_source_state", {"input_event_id": self.EVENT_ID})])

    def test_get_missing_source_returns_none_and_version_zero(self):
        state = SupabaseOfficialReleaseSourceRepository(self._state_client(active=False, version=0)).get_state(self.EVENT_ID)
        self.assertIsNone(state.source)
        self.assertEqual(state.version, 0)

    def test_get_tombstone_returns_none_but_preserves_version(self):
        state = SupabaseOfficialReleaseSourceRepository(self._state_client(active=False, version=4)).get_state(self.EVENT_ID)
        self.assertIsNone(state.source)
        self.assertEqual(state.version, 4)

    def test_unaudited_active_source_is_returned_inactive_by_state_rpc(self):
        state = SupabaseOfficialReleaseSourceRepository(self._state_client(active=False, version=7)).get_state(self.EVENT_ID)
        self.assertIsNone(state.source)
        self.assertEqual(state.version, 7)

    def test_get_malformed_persisted_source_fails_closed(self):
        repository = SupabaseOfficialReleaseSourceRepository(self._state_client(active=True, version=1, kind="direct_url", url="not-a-url"))
        with self.assertRaisesRegex(RuntimeError, "row is malformed"):
            repository.get(self.EVENT_ID)

    def test_set_uses_atomic_rpc_with_expected_version(self):
        client = _Client(rpc_rows=[{
            "out_event_id": self.EVENT_ID,
            "out_source_kind": "direct_url",
            "out_source_url": "https://investor.example.com/fy2026.pdf",
            "out_source_title": "FY2026",
            "out_version": 2,
            "out_created_at": "2026-08-25T17:00:00+00:00",
            "out_updated_at": "2026-08-25T18:00:00+00:00",
        }])
        repository = SupabaseOfficialReleaseSourceRepository(client)
        source = OfficialReleaseSource(
            event_id=self.EVENT_ID,
            source_kind="direct_url",
            source_url="https://investor.example.com/fy2026.pdf",
            source_title="FY2026",
        )

        saved = repository.set(source, expected_version=1, actor="marko")

        self.assertEqual(saved.version, 2)
        self.assertEqual(len(client.rpc_calls), 1)
        rpc_name, payload = client.rpc_calls[0]
        self.assertEqual(rpc_name, "set_event_official_release_source_approved")
        self.assertEqual(payload["input_event_id"], self.EVENT_ID)
        self.assertEqual(payload["input_source_kind"], "direct_url")
        self.assertEqual(payload["input_source_url"], "https://investor.example.com/fy2026.pdf")
        self.assertEqual(payload["input_expected_version"], 1)
        self.assertEqual(payload["input_actor"], "marko")

    def test_set_requires_explicit_nonnegative_expected_version(self):
        repository = SupabaseOfficialReleaseSourceRepository(_Client())
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "direct_url",
            "https://investor.example.com/fy2026.pdf",
        )
        with self.assertRaisesRegex(ValueError, "zero or positive"):
            repository.set(source, expected_version=-1, actor="marko")

    def test_sql_input_validation_marker_becomes_value_error(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("22023", "invalid_source_url"))
        )
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "direct_url",
            "https://investor.example.com/fy2026.pdf",
        )
        with self.assertRaisesRegex(ValueError, "input is invalid"):
            repository.set(source, expected_version=0, actor="marko")

    def test_unmarked_22023_stays_service_failure(self):
        repository = SupabaseOfficialReleaseSourceRepository(
            _ErrorClient(_ApiError("22023", "unexpected database validation failure"))
        )
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "direct_url",
            "https://investor.example.com/fy2026.pdf",
        )
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            repository.set(source, expected_version=0, actor="marko")

    def test_clear_uses_atomic_rpc_and_returns_advanced_version(self):
        client = _Client(rpc_rows=4)
        repository = SupabaseOfficialReleaseSourceRepository(client)

        new_version = repository.clear(self.EVENT_ID, expected_version=3, actor="marko")

        self.assertEqual(new_version, 4)
        self.assertEqual(client.rpc_calls, [(
            "clear_event_official_release_source_approved",
            {
                "input_event_id": self.EVENT_ID,
                "input_expected_version": 3,
                "input_actor": "marko",
            },
        )])

    def test_clear_requires_positive_expected_version(self):
        repository = SupabaseOfficialReleaseSourceRepository(_Client())
        with self.assertRaisesRegex(ValueError, "expected_version must be positive"):
            repository.clear(self.EVENT_ID, expected_version=0, actor="marko")

    def test_clear_must_advance_version(self):
        repository = SupabaseOfficialReleaseSourceRepository(_Client(rpc_rows=3))
        with self.assertRaisesRegex(RuntimeError, "advance the version"):
            repository.clear(self.EVENT_ID, expected_version=3, actor="marko")


if __name__ == "__main__":
    unittest.main()
