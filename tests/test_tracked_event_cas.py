from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from trading_system.tracked_event_cas import fail_tracked_event_if_current


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.payload = None

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def is_(self, column, value):
        self.filters.append(("is", column, value))
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Client:
    def __init__(self, rows):
        self.query = _Query(rows)
        self.table_name = None

    def table(self, name):
        self.table_name = name
        return self.query


class _Repository:
    def __init__(self, rows):
        self.client = _Client(rows)


class TrackedEventCasTests(unittest.TestCase):
    def test_failure_is_bound_to_version_status_and_missing_reference(self) -> None:
        expected = datetime(2026, 8, 25, 5, 0, tzinfo=UTC)
        repository = _Repository([{"id": "event-1"}])

        fail_tracked_event_if_current(
            repository,
            event_id="event-1",
            expected_event_updated_at=expected,
            actor="tracked-event-worker",
            error="stale pre-event market context",
        )

        self.assertEqual(repository.client.table_name, "tracked_market_events")
        self.assertIn(("eq", "id", "event-1"), repository.client.query.filters)
        self.assertIn(
            ("eq", "updated_at", expected.isoformat()),
            repository.client.query.filters,
        )
        self.assertIn(("eq", "status", "tracked"), repository.client.query.filters)
        self.assertIn(("is", "reference_price", "null"), repository.client.query.filters)
        self.assertEqual(repository.client.query.payload["status"], "failed")

    def test_concurrent_progress_is_retryable_conflict(self) -> None:
        repository = _Repository([])
        with self.assertRaisesRegex(RuntimeError, "changed before the version-bound failure"):
            fail_tracked_event_if_current(
                repository,
                event_id="event-1",
                expected_event_updated_at=datetime(2026, 8, 25, 5, 0, tzinfo=UTC),
                actor="tracked-event-worker",
                error="stale pre-event market context",
            )

    def test_expected_version_must_be_timezone_aware(self) -> None:
        repository = _Repository([{"id": "event-1"}])
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            fail_tracked_event_if_current(
                repository,
                event_id="event-1",
                expected_event_updated_at=datetime(2026, 8, 25, 5, 0),
                actor="tracked-event-worker",
                error="stale pre-event market context",
            )


if __name__ == "__main__":
    unittest.main()
