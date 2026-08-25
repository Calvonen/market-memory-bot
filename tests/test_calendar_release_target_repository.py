from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from trading_system.calendar_release_worker import SupabaseCalendarReleaseTargetRepository


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def select(self, value):
        self.calls.append(("select", value))
        return self

    def eq(self, key, value):
        self.calls.append(("eq", key, value))
        return self

    def gte(self, key, value):
        self.calls.append(("gte", key, value))
        return self

    def lte(self, key, value):
        self.calls.append(("lte", key, value))
        return self

    def order(self, key):
        self.calls.append(("order", key))
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Client:
    def __init__(self, rows):
        self.query = _Query(rows)
        self.table_names = []

    def table(self, name):
        self.table_names.append(name)
        return self.query


class CalendarReleaseTargetRepositoryTests(unittest.TestCase):
    def test_requests_tracked_usa_earnings_through_end_date_without_lower_bound(self):
        client = _Client(
            [
                {
                    "id": "22648076-6e43-40fc-ac6e-f57a79ceee31",
                    "instrument": "dks",
                    "scheduled_date": "2026-08-23",
                }
            ]
        )
        repository = SupabaseCalendarReleaseTargetRepository(client)

        targets = repository.list_targets(
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 25),
        )

        self.assertEqual(client.table_names, ["calendar_events"])
        self.assertIn(("eq", "status", "tracked"), client.query.calls)
        self.assertIn(("eq", "market", "USA"), client.query.calls)
        self.assertIn(("eq", "event_type", "earnings"), client.query.calls)
        self.assertNotIn(("gte", "scheduled_date", "2026-08-24"), client.query.calls)
        self.assertIn(("lte", "scheduled_date", "2026-08-25"), client.query.calls)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].ticker, "DKS")
        self.assertEqual(targets[0].scheduled_date, date(2026, 8, 23))
        self.assertEqual(
            targets[0].event_id,
            "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31",
        )


if __name__ == "__main__":
    unittest.main()
