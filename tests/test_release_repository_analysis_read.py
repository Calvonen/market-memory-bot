from __future__ import annotations

import unittest
from types import SimpleNamespace

from trading_system.release_repository import SupabaseReleaseRepository


class Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self.limit_value = None

    def select(self, *_args):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        self.rows = [row for row in self.rows if row.get(column) == value]
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        self.rows = self.rows[:value]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class Client:
    def __init__(self, rows):
        self.rows = rows
        self.last_query = None

    def table(self, name):
        if name != "event_ai_analyses":
            raise AssertionError(name)
        self.last_query = Query(self.rows)
        return self.last_query


def row(analysis_id: str):
    return {
        "id": analysis_id,
        "event_id": "tracked:event-1",
        "expectation_version": 4,
        "source_document_id": "doc-1",
    }


class ReleaseRepositoryAnalysisReadTests(unittest.TestCase):
    def test_zero_rows_means_analysis_not_ready(self) -> None:
        client = Client([])
        repo = SupabaseReleaseRepository(client)
        self.assertIsNone(
            repo.get_analysis_for_event_version(
                event_id="tracked:event-1", expectation_version=4
            )
        )
        self.assertEqual(client.last_query.limit_value, 2)

    def test_single_exact_event_version_row_is_returned(self) -> None:
        expected = row("analysis-1")
        client = Client([expected, {**row("other"), "expectation_version": 3}])
        repo = SupabaseReleaseRepository(client)
        actual = repo.get_analysis_for_event_version(
            event_id="tracked:event-1", expectation_version=4
        )
        self.assertIs(actual, expected)
        self.assertIn(("event_id", "tracked:event-1"), client.last_query.filters)
        self.assertIn(("expectation_version", 4), client.last_query.filters)

    def test_multiple_rows_for_same_event_version_fail_closed(self) -> None:
        repo = SupabaseReleaseRepository(Client([row("analysis-1"), row("analysis-2")]))
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            repo.get_analysis_for_event_version(
                event_id="tracked:event-1", expectation_version=4
            )


if __name__ == "__main__":
    unittest.main()
