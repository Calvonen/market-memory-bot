from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from trading_system.calendar_release_worker import (
    TARGET_PAGE_SIZE,
    SupabaseCalendarReleaseTargetRepository,
)


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

    def gt(self, key, value):
        self.calls.append(("gt", key, value))
        return self

    def order(self, key):
        self.calls.append(("order", key))
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Client:
    def __init__(self, pages):
        self.pages = list(pages)
        self.queries = []
        self.table_names = []

    def table(self, name):
        self.table_names.append(name)
        index = len(self.queries)
        rows = self.pages[index] if index < len(self.pages) else []
        query = _Query(rows)
        self.queries.append(query)
        return query


class CalendarReleaseTargetRepositoryTests(unittest.TestCase):
    def test_requests_tracked_usa_earnings_through_end_date_without_lower_bound(self):
        client = _Client(
            [[
                {
                    "id": "22648076-6e43-40fc-ac6e-f57a79ceee31",
                    "instrument": "dks",
                    "scheduled_date": "2026-08-23",
                }
            ]]
        )
        repository = SupabaseCalendarReleaseTargetRepository(client)

        targets = repository.list_targets(
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 25),
        )

        self.assertEqual(client.table_names, ["calendar_events"])
        calls = client.queries[0].calls
        self.assertIn(("eq", "status", "tracked"), calls)
        self.assertIn(("eq", "market", "USA"), calls)
        self.assertIn(("eq", "event_type", "earnings"), calls)
        self.assertNotIn(("gte", "scheduled_date", "2026-08-24"), calls)
        self.assertIn(("lte", "scheduled_date", "2026-08-25"), calls)
        self.assertIn(("order", "id"), calls)
        self.assertIn(("limit", TARGET_PAGE_SIZE), calls)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].ticker, "DKS")
        self.assertEqual(targets[0].scheduled_date, date(2026, 8, 23))
        self.assertEqual(
            targets[0].event_id,
            "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31",
        )

    def test_paginates_past_postgrest_response_limit_with_id_cursor(self):
        first_page = [
            {
                "id": f"{index:08d}-0000-0000-0000-000000000000",
                "instrument": "AAA",
                "scheduled_date": "2026-08-20",
            }
            for index in range(TARGET_PAGE_SIZE)
        ]
        second_id = "99999999-0000-0000-0000-000000000000"
        second_page = [
            {
                "id": second_id,
                "instrument": "NEW",
                "scheduled_date": "2026-08-25",
            }
        ]
        client = _Client([first_page, second_page])
        repository = SupabaseCalendarReleaseTargetRepository(client)

        targets = repository.list_targets(
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 25),
        )

        self.assertEqual(len(client.queries), 2)
        self.assertEqual(len(targets), TARGET_PAGE_SIZE + 1)
        self.assertEqual(targets[-1].ticker, "NEW")
        self.assertIn(
            ("gt", "id", first_page[-1]["id"]),
            client.queries[1].calls,
        )


if __name__ == "__main__":
    unittest.main()
