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

    def upsert(self, payload, on_conflict=None):
        self.calls.append(("upsert", payload, on_conflict))
        self.response_rows = [dict(payload)]
        return self

    def delete(self):
        self.calls.append(("delete",))
        return self

    def execute(self):
        self.calls.append(("execute",))
        return SimpleNamespace(data=self.response_rows)


class _Client:
    def __init__(self, *, response_rows=None):
        self.response_rows = response_rows
        self.queries = []

    def table(self, name):
        query = _Query(response_rows=self.response_rows)
        query.calls.append(("table", name))
        self.queries.append(query)
        return query


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
        with self.assertRaisesRegex(ValueError, "without credentials"):
            OfficialReleaseSource(self.EVENT_ID, "direct_url", "https://user:pass@example.com/results")

    def test_get_returns_validated_canonical_source(self):
        client = _Client(response_rows=[{
            "event_id": self.EVENT_ID,
            "source_kind": "results_page",
            "source_url": "https://investor.example.com/results",
            "source_title": "Investor results",
        }])
        repository = SupabaseOfficialReleaseSourceRepository(client)

        source = repository.get(self.EVENT_ID)

        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.source_kind, "results_page")
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
        }]))
        with self.assertRaisesRegex(RuntimeError, "row is malformed"):
            repository.get(self.EVENT_ID)

    def test_set_upserts_exact_user_approved_values_by_event_id(self):
        client = _Client()
        repository = SupabaseOfficialReleaseSourceRepository(client)
        source = OfficialReleaseSource(
            event_id=self.EVENT_ID,
            source_kind="direct_url",
            source_url="https://investor.example.com/fy2026.pdf",
            source_title="FY2026",
        )

        saved = repository.set(source)

        self.assertEqual(saved, source)
        upsert_calls = [call for call in client.queries[0].calls if call[0] == "upsert"]
        self.assertEqual(len(upsert_calls), 1)
        payload = upsert_calls[0][1]
        self.assertEqual(payload["event_id"], self.EVENT_ID)
        self.assertEqual(payload["source_kind"], "direct_url")
        self.assertEqual(payload["source_url"], "https://investor.example.com/fy2026.pdf")
        self.assertEqual(upsert_calls[0][2], "event_id")

    def test_clear_deletes_only_the_canonical_event_source(self):
        client = _Client()
        repository = SupabaseOfficialReleaseSourceRepository(client)

        repository.clear(self.EVENT_ID)

        self.assertIn(("delete",), client.queries[0].calls)
        self.assertIn(("eq", "event_id", self.EVENT_ID), client.queries[0].calls)


if __name__ == "__main__":
    unittest.main()
