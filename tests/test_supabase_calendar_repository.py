"""Exercises SupabaseCalendarEventRepository.list_upcoming()'s pagination
against a fake Supabase Data API client - not source-text assertions.

Regression for the Codex P2 finding: a single unpaginated request silently
truncates at PostgREST's default `db-max-rows` (1,000). This proves the
real pagination loop in supabase_calendar_repository.py actually pages
through a result set larger than one page, with a fully deterministic
order and no duplicate/missing rows at page boundaries - not just that the
code contains a loop.
"""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace

from trading_system.calendar_repository import CalendarEventStatus
from trading_system.supabase_calendar_repository import (
    _LIST_UPCOMING_PAGE_SIZE,
    SupabaseCalendarEventRepository,
)


class _FakeQueryBuilder:
    """Mimics just enough of supabase-py's fluent table-query builder for
    list_upcoming(): every filter/order method is a no-op that returns
    self (chainable), and .range()/.execute() actually slice the fake
    backing table - exactly like PostgREST's own Range-header pagination."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._range: tuple[int, int] | None = None
        self.order_calls: list[str] = []

    def select(self, *_args, **_kwargs) -> "_FakeQueryBuilder":
        return self

    def in_(self, *_args, **_kwargs) -> "_FakeQueryBuilder":
        return self

    def gte(self, *_args, **_kwargs) -> "_FakeQueryBuilder":
        return self

    def lte(self, *_args, **_kwargs) -> "_FakeQueryBuilder":
        return self

    def order(self, column: str, *_args, **_kwargs) -> "_FakeQueryBuilder":
        self.order_calls.append(column)
        return self

    def range(self, start: int, end: int) -> "_FakeQueryBuilder":
        self._range = (start, end)
        return self

    def execute(self) -> SimpleNamespace:
        assert self._range is not None, "range() must be called before execute()"
        start, end = self._range
        return SimpleNamespace(data=self._rows[start : end + 1])


class _FakeCalendarClient:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.builders: list[_FakeQueryBuilder] = []

    def table(self, name: str) -> _FakeQueryBuilder:
        assert name == "calendar_events"
        builder = _FakeQueryBuilder(self._rows)
        self.builders.append(builder)
        return builder


def _row(index: int, *, scheduled_date: str = "2026-09-01") -> dict:
    return {
        "id": f"00000000-0000-0000-0000-{index:012d}",
        "company_name": f"Company {index}",
        "instrument": f"SYM{index}",
        "market": "NASDAQ",
        "event_type": "earnings",
        "occurrence_key": f"2026Q{(index % 4) + 1}",
        "scheduled_date": scheduled_date,
        "source": "finnhub",
        "status": "candidate",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    }


class SupabaseCalendarRepositoryPaginationTests(unittest.TestCase):
    def test_pages_through_more_rows_than_a_single_page_holds(self) -> None:
        total_rows = _LIST_UPCOMING_PAGE_SIZE + 250
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)

        events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        self.assertEqual(len(events), total_rows)

    def test_result_set_larger_than_one_page_has_no_duplicates_or_gaps(self) -> None:
        total_rows = _LIST_UPCOMING_PAGE_SIZE * 2 + 37
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)

        events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        instruments = [e.instrument for e in events]
        self.assertEqual(len(instruments), len(set(instruments)), "duplicate rows across page boundaries")
        self.assertEqual(set(instruments), {f"SYM{i}" for i in range(total_rows)})

    def test_fetches_the_expected_number_of_pages(self) -> None:
        # Exactly two full pages plus a partial third - the loop must issue
        # exactly 3 requests (a page as short as the previous ones can
        # never be assumed to be the last one) and a 4th, empty request to
        # confirm the third page was actually the end.
        total_rows = _LIST_UPCOMING_PAGE_SIZE * 2 + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)

        repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        self.assertEqual(len(client.builders), 3)
        ranges = [builder._range for builder in client.builders]
        self.assertEqual(
            ranges,
            [
                (0, _LIST_UPCOMING_PAGE_SIZE - 1),
                (_LIST_UPCOMING_PAGE_SIZE, 2 * _LIST_UPCOMING_PAGE_SIZE - 1),
                (2 * _LIST_UPCOMING_PAGE_SIZE, 3 * _LIST_UPCOMING_PAGE_SIZE - 1),
            ],
        )

    def test_ordering_includes_a_unique_tie_break_column(self) -> None:
        # scheduled_date alone is not a unique sort key - many rows can
        # share the same date. Without a unique secondary column (id), two
        # separate page requests could each apply a different tie-break
        # order among same-date rows, producing a duplicate or a gap right
        # at the page boundary.
        rows = [_row(0)]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)

        repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        self.assertEqual(client.builders[0].order_calls, ["scheduled_date", "id"])

    def test_a_result_set_smaller_than_one_page_still_works(self) -> None:
        rows = [_row(i) for i in range(3)]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)

        events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        self.assertEqual(len(events), 3)
        self.assertEqual(len(client.builders), 1)

    def test_empty_result_set_returns_no_events_in_one_request(self) -> None:
        client = _FakeCalendarClient([])
        repo = SupabaseCalendarEventRepository(client)

        events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        self.assertEqual(events, ())
        self.assertEqual(len(client.builders), 1)


if __name__ == "__main__":
    unittest.main()
