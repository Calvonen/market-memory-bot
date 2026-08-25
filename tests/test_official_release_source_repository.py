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
    def __init__(self, *, response_rows=None, rpc_rows=None):
        self.response_rows = response_rows
        self.rpc_rows = [] if rpc_rows is None else rpc_rows
        self.queries = []
        self.rpc_calls = []

    def table(self, name):
        query = _Query(response_rows=self.response_rows)
        query.calls.append(("table", name))
        self.queries.append(query)
        return query

    def rpc(self, name, payload):
        self.rpc_calls.append((name, payload))
        return _RpcQuery(self.rpc_rows)


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
        with self.assertRaisesRegex(ValueError, "no credentials"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://user:pass@example.com/results")
        with self.assertRaisesRegex(ValueError, "valid host"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://:")
        with self.assertRaisesRegex(ValueError, "valid port"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://example.com:99999/results")
        with self.assertRaisesRegex(ValueError, "version must be positive"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://example.com/results", version=0)

    def test_get_returns_validated_canonical_source(self):
        client = _Client(response_rows=[{
            "event_id": self.EVENT_ID,
            "source_kind": "results_page",
            "source_url": "https://investor.example.com/results",
            "source_title": "Investor results",
            "version": 3,
        }])
        repository = SupabaseOfficialReleaseSourceRepository(client)

        source = repository.get(self.EVENT_ID)

        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.source_kind, "results_page")
        self.assertEqual(source.version, 3)
        self.assertIn(("eq", "event_id", self.EVENT_ID), client.queries[0].calls)
        self.assertIn(("limit", 1), client.queries[0].calls)

    def test_get_missing_source_returns_none(self):
        repository = SupabaseOfficialReleaseSourceRepository(_Client(response_rows=[]))
        self.assertIsNone(repository.get(self.EVENT_ID))

    def test_get_malformed_persisted_source_fails_closed(self):
        repository = SupabaseOfficialReleaseSourceRepository(_Client(response_rows=[{
            "event_id": self.EVENT_ID,
            "source_kind": "direct_url",
            "source_url": "not-a-url",
            "source_title": None,
            "version": 1,
        }]))
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

        saved = repository.set(source, expected_version=1)

        self.assertEqual(saved.version, 2)
        self.assertEqual(len(client.rpc_calls), 1)
        rpc_name, payload = client.rpc_calls[0]
        self.assertEqual(rpc_name, "set_event_official_release_source")
        self.assertEqual(payload["input_event_id"], self.EVENT_ID)
        self.assertEqual(payload["input_source_kind"], "direct_url")
        self.assertEqual(payload["input_source_url"], "https://investor.example.com/fy2026.pdf")
        self.assertEqual(payload["input_expected_version"], 1)

    def test_set_requires_explicit_nonnegative_expected_version(self):
        repository = SupabaseOfficialReleaseSourceRepository(_Client())
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "direct_url",
            "https://investor.example.com/fy2026.pdf",
        )
        with self.assertRaisesRegex(ValueError, "zero or positive"):
            repository.set(source, expected_version=-1)

    def test_clear_uses_atomic_rpc_with_expected_version(self):
        client = _Client(rpc_rows=True)
        repository = SupabaseOfficialReleaseSourceRepository(client)

        repository.clear(self.EVENT_ID, expected_version=3)

        self.assertEqual(client.rpc_calls, [(
            "clear_event_official_release_source",
            {
                "input_event_id": self.EVENT_ID,
                "input_expected_version": 3,
            },
        )])

    def test_clear_requires_positive_expected_version(self):
        repository = SupabaseOfficialReleaseSourceRepository(_Client())
        with self.assertRaisesRegex(ValueError, "expected_version must be positive"):
            repository.clear(self.EVENT_ID, expected_version=0)


if __name__ == "__main__":
    unittest.main()
