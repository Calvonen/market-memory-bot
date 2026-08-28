from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from trading_system.calendar_release_worker import (
    RELEASE_ELIGIBLE_TRACKED_STATUSES,
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

    def in_(self, key, value):
        self.calls.append(("in", key, tuple(value)))
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


class _RpcCall:
    def __init__(self, rows):
        self.rows = rows

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Client:
    def __init__(self, pages, *, rpc_rows=None):
        self.pages = list(pages)
        self.queries = []
        self.table_names = []
        self.rpc_rows = rpc_rows or []
        self.rpc_calls = []

    def table(self, name):
        self.table_names.append(name)
        index = len(self.queries)
        rows = self.pages[index] if index < len(self.pages) else []
        query = _Query(rows)
        self.queries.append(query)
        return query

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return _RpcCall(self.rpc_rows)


class CalendarReleaseTargetRepositoryTests(unittest.TestCase):
    def test_requests_canonical_earnings_across_markets_without_lower_bound(self):
        client = _Client(
            [[
                {
                    "id": "12648076-6e43-40fc-ac6e-f57a79ceee31",
                    "calendar_event_id": "22648076-6e43-40fc-ac6e-f57a79ceee31",
                    "instrument": "aapl",
                    "event_date": "2026-08-23",
                    "market": "NASDAQ",
                },
                {
                    "id": "32648076-6e43-40fc-ac6e-f57a79ceee31",
                    "calendar_event_id": None,
                    "instrument": "hvn.asx",
                    "event_date": "2026-08-24",
                    "market": "australia",
                },
            ]]
        )
        repository = SupabaseCalendarReleaseTargetRepository(client)

        targets = repository.list_targets(
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 25),
        )

        self.assertEqual(client.table_names, ["tracked_market_events"])
        calls = client.queries[0].calls
        self.assertIn(
            ("select", "id,calendar_event_id,instrument,event_date,market"),
            calls,
        )
        self.assertIn(("eq", "kind", "earnings"), calls)
        self.assertIn(("in", "status", RELEASE_ELIGIBLE_TRACKED_STATUSES), calls)
        self.assertNotIn(("gte", "event_date", "2026-08-24"), calls)
        self.assertIn(("lte", "event_date", "2026-08-25"), calls)
        self.assertIn(("order", "id"), calls)
        self.assertIn(("limit", TARGET_PAGE_SIZE), calls)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].ticker, "AAPL")
        self.assertEqual(targets[0].market, "NASDAQ")
        self.assertEqual(targets[0].scheduled_date, date(2026, 8, 23))
        self.assertEqual(
            targets[0].event_id,
            "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31",
        )
        self.assertEqual(targets[1].ticker, "HVN.ASX")
        self.assertEqual(targets[1].market, "AUSTRALIA")
        self.assertEqual(
            targets[1].event_id,
            "tracked:32648076-6e43-40fc-ac6e-f57a79ceee31",
        )
        self.assertEqual(
            targets[1].tracked_event_id,
            "32648076-6e43-40fc-ac6e-f57a79ceee31",
        )

    def test_paginates_past_postgrest_response_limit_with_id_cursor(self):
        first_page = [
            {
                "id": f"{index:08d}-0000-0000-0000-000000000000",
                "calendar_event_id": None,
                "instrument": "AAA",
                "event_date": "2026-08-20",
                "market": "NASDAQ",
            }
            for index in range(TARGET_PAGE_SIZE)
        ]
        second_id = "99999999-0000-0000-0000-000000000000"
        second_page = [
            {
                "id": second_id,
                "calendar_event_id": None,
                "instrument": "NEW",
                "event_date": "2026-08-25",
                "market": "ASX",
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
        self.assertEqual(targets[-1].market, "ASX")
        self.assertIn(("gt", "id", first_page[-1]["id"]), client.queries[1].calls)

    def test_incomplete_target_row_fails_closed(self):
        client = _Client(
            [[{
                "id": "22648076-6e43-40fc-ac6e-f57a79ceee31",
                "calendar_event_id": None,
                "instrument": "",
                "event_date": "2026-08-25",
                "market": "NASDAQ",
            }]]
        )
        repository = SupabaseCalendarReleaseTargetRepository(client)

        with self.assertRaisesRegex(
            RuntimeError,
            "22648076-6e43-40fc-ac6e-f57a79ceee31.*missing required canonical data",
        ):
            repository.list_targets(
                start_date=date(2026, 8, 24),
                end_date=date(2026, 8, 25),
            )

    def test_missing_market_fails_closed(self):
        client = _Client(
            [[{
                "id": "22648076-6e43-40fc-ac6e-f57a79ceee31",
                "calendar_event_id": None,
                "instrument": "NOKIA",
                "event_date": "2026-08-25",
                "market": "",
            }]]
        )
        repository = SupabaseCalendarReleaseTargetRepository(client)

        with self.assertRaisesRegex(RuntimeError, "missing required canonical data"):
            repository.list_targets(
                start_date=date(2026, 8, 24),
                end_date=date(2026, 8, 25),
            )

    def test_ensure_shell_uses_canonical_tracked_event_identity(self):
        tracked_id = "32648076-6e43-40fc-ac6e-f57a79ceee31"
        client = _Client(
            [[]],
            rpc_rows=[{
                "out_release_event_id": f"tracked:{tracked_id}",
                "out_action": "inserted",
            }],
        )
        repository = SupabaseCalendarReleaseTargetRepository(client)
        target = type(
            "Target",
            (),
            {
                "tracked_event_id": tracked_id,
                "event_id": f"tracked:{tracked_id}",
            },
        )()

        release_id = repository.ensure_release_shell(target)

        self.assertEqual(release_id, f"tracked:{tracked_id}")
        self.assertEqual(
            client.rpc_calls,
            [
                (
                    "ensure_tracked_event_release_shell",
                    {"input_tracked_event_id": tracked_id},
                )
            ],
        )

    def test_ensure_shell_fails_closed_on_identity_mismatch(self):
        tracked_id = "32648076-6e43-40fc-ac6e-f57a79ceee31"
        client = _Client(
            [[]],
            rpc_rows=[{
                "out_release_event_id": "tracked:other",
                "out_action": "noop_existing",
            }],
        )
        repository = SupabaseCalendarReleaseTargetRepository(client)
        target = type(
            "Target",
            (),
            {
                "tracked_event_id": tracked_id,
                "event_id": f"tracked:{tracked_id}",
            },
        )()

        with self.assertRaisesRegex(RuntimeError, "identity differs"):
            repository.ensure_release_shell(target)


if __name__ == "__main__":
    unittest.main()
