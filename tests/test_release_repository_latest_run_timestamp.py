from __future__ import annotations

import unittest

from trading_system.release_repository import SupabaseReleaseRepository


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows, calls):
        self.rows = list(rows)
        self.calls = calls

    def select(self, fields):
        self.calls.append(("select", fields))
        return self

    def eq(self, field, value):
        self.calls.append(("eq", field, value))
        return self

    def order(self, field, desc=False):
        self.calls.append(("order", field, desc))
        self.rows.sort(key=lambda row: row.get(field) or "", reverse=desc)
        return self

    def limit(self, count):
        self.calls.append(("limit", count))
        self.rows = self.rows[:count]
        return self

    def execute(self):
        return _Response(self.rows)


class _Client:
    def __init__(self):
        self.calls = []
        self.rows = [
            {
                "event_id": "tracked:event",
                "provider": "canonical_release_worker",
                "status": "error",
                "error_message": "older",
                "checked_at": "2026-09-01T05:00:00+00:00",
            },
            {
                "event_id": "tracked:event",
                "provider": "canonical_release_worker",
                "status": "validated",
                "error_message": None,
                "checked_at": "2026-09-01T05:01:00+00:00",
            },
        ]

    def table(self, name):
        self.calls.append(("table", name))
        return _Query(self.rows, self.calls)


class ReleaseRepositoryLatestRunTimestampTests(unittest.TestCase):
    def test_latest_run_uses_production_checked_at_column(self):
        client = _Client()

        row = SupabaseReleaseRepository(client).latest_run(event_id="tracked:event")

        self.assertIn(
            ("select", "provider,status,error_message,checked_at"), client.calls
        )
        self.assertIn(("order", "checked_at", True), client.calls)
        self.assertEqual(row["status"], "validated")
        self.assertEqual(row["created_at"], "2026-09-01T05:01:00+00:00")


if __name__ == "__main__":
    unittest.main()
