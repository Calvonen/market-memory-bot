"""Exercises SupabaseCalendarEventRepository.list_upcoming()'s pagination
against a fake Supabase Data API client - not source-text assertions.

Regression for two Codex findings:

- P2 (round 6): a single unpaginated request silently truncates at
  PostgREST's default `db-max-rows` (1,000).
- P2 (this round): offset-based pagination (`.range(offset, ...)`) is not
  safe against concurrent writes - a row landing ahead of the current
  offset boundary shifts every later row's position, so the next
  fixed-offset request either re-returns an already-seen row (duplicate)
  or skips the row that used to sit at that boundary (never returned by
  either page). This proves the real pagination loop in
  supabase_calendar_repository.py uses keyset (cursor) pagination instead
  - anchored to the actual `(scheduled_date, id)` of the last row the
  previous page returned, never a numeric offset a concurrent write can
  shift - with a fully deterministic order and no duplicate/missing rows
  at page boundaries, even under concurrent mutation.
"""

from __future__ import annotations

import re
import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from trading_system.calendar_repository import CalendarEventStatus
from trading_system.supabase_calendar_repository import (
    _LIST_UPCOMING_PAGE_SIZE,
    SupabaseCalendarEventRepository,
)

# Matches exactly the shape list_upcoming() builds for a keyset cursor:
# "scheduled_date.gt.<date>,and(scheduled_date.eq.<date>,id.gt.<id>)".
_CURSOR_OR_RE = re.compile(
    r"^scheduled_date\.gt\.(?P<date>[^,]+),"
    r"and\(scheduled_date\.eq\.(?P<date2>[^,]+),id\.gt\.(?P<id>[^)]+)\)$"
)


class _FakeQueryBuilder:
    """Mimics just enough of supabase-py's fluent table-query builder for
    list_upcoming(): filter methods actually filter (against the table's
    *live* row list, not a snapshot), so a test can mutate the table
    in between page fetches to simulate a concurrent insert/update - the
    same thing a real concurrent sync or track() call would do against a
    real Postgres table mid-pagination."""

    def __init__(self, table: "_FakeCalendarClient") -> None:
        self._table = table
        self._status_in: list[str] | None = None
        self._gte: tuple[str, str] | None = None
        self._lte: tuple[str, str] | None = None
        self._or_expr: str | None = None
        self._limit: int | None = None
        self.order_calls: list[str] = []

    def select(self, *_args, **_kwargs) -> "_FakeQueryBuilder":
        return self

    def in_(self, column: str, values) -> "_FakeQueryBuilder":
        assert column == "status"
        self._status_in = list(values)
        return self

    def gte(self, column: str, value: str) -> "_FakeQueryBuilder":
        assert column == "scheduled_date"
        self._gte = (column, value)
        return self

    def lte(self, column: str, value: str) -> "_FakeQueryBuilder":
        assert column == "scheduled_date"
        self._lte = (column, value)
        return self

    def or_(self, filters: str) -> "_FakeQueryBuilder":
        self._or_expr = filters
        return self

    def order(self, column: str, *_args, **_kwargs) -> "_FakeQueryBuilder":
        self.order_calls.append(column)
        return self

    def limit(self, size: int) -> "_FakeQueryBuilder":
        self._limit = size
        return self

    def execute(self) -> SimpleNamespace:
        assert self._limit is not None, "limit() must be called before execute()"
        # Reads the table's *current* rows, not a snapshot taken when the
        # builder was constructed - this is what lets a test simulate a
        # concurrent insert/update landing between two page fetches.
        matched = [row for row in self._table.rows if self._matches(row)]
        matched.sort(key=lambda row: (row["scheduled_date"], row["id"]))
        return SimpleNamespace(data=matched[: self._limit])

    def _matches(self, row: dict) -> bool:
        if self._status_in is not None and row["status"] not in self._status_in:
            return False
        if self._gte is not None and row["scheduled_date"] < self._gte[1]:
            return False
        if self._lte is not None and row["scheduled_date"] > self._lte[1]:
            return False
        if self._or_expr is not None:
            match = _CURSOR_OR_RE.match(self._or_expr)
            assert match, f"unrecognized or_() expression: {self._or_expr!r}"
            cursor_date, cursor_id = match.group("date"), match.group("id")
            after_cursor = row["scheduled_date"] > cursor_date or (
                row["scheduled_date"] == cursor_date and row["id"] > cursor_id
            )
            if not after_cursor:
                return False
        return True


class _FakeCalendarClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows  # mutable + live - see _FakeQueryBuilder.execute()
        self.builders: list[_FakeQueryBuilder] = []

    def table(self, name: str) -> _FakeQueryBuilder:
        assert name == "calendar_events"
        builder = _FakeQueryBuilder(self)
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
        # never be assumed to be the last one) and stop there, since the
        # third page is shorter than a full page.
        total_rows = _LIST_UPCOMING_PAGE_SIZE * 2 + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)

        repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        self.assertEqual(len(client.builders), 3)
        # No numeric offset anywhere - the first page has no cursor filter
        # at all, and every later page's cursor is the *value* of the
        # previous page's own last row, not a running count.
        self.assertIsNone(client.builders[0]._or_expr)
        for builder in client.builders[1:]:
            self.assertIsNotNone(builder._or_expr)
        self.assertEqual([b._limit for b in client.builders], [_LIST_UPCOMING_PAGE_SIZE] * 3)

    def test_second_page_cursor_is_the_last_row_of_the_first_page(self) -> None:
        total_rows = _LIST_UPCOMING_PAGE_SIZE + 5
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)

        repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        last_row_of_page_one = rows[_LIST_UPCOMING_PAGE_SIZE - 1]
        expected = (
            f"scheduled_date.gt.{last_row_of_page_one['scheduled_date']},"
            f"and(scheduled_date.eq.{last_row_of_page_one['scheduled_date']},"
            f"id.gt.{last_row_of_page_one['id']})"
        )
        self.assertEqual(client.builders[1]._or_expr, expected)

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

    # -- concurrent change mid-pagination (P2 regression) -------------------

    def test_concurrent_insert_behind_the_cursor_never_skips_a_remaining_row(self) -> None:
        # The classic offset-pagination bug: a row lands ahead of the
        # current offset boundary between two page fetches, shifting every
        # later row's absolute position by one. A fixed-offset next
        # request then starts one row too early or too late, silently
        # skipping whatever row used to sit at that boundary. Keyset
        # pagination has no numeric offset to shift - page 2's cursor is
        # the literal (scheduled_date, id) of page 1's actual last row, so
        # a concurrent insert elsewhere in the table can never move it.
        page_size = _LIST_UPCOMING_PAGE_SIZE
        total_rows = page_size + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(list(rows))
        repo = SupabaseCalendarEventRepository(client)

        real_execute = _FakeQueryBuilder.execute
        call_count = {"n": 0}
        # The cursor page 2 will use is the literal (scheduled_date, id) of
        # page 1's actual last row (index page_size - 1).
        cursor_id = f"00000000-0000-0000-0000-{page_size - 1:012d}"
        new_row_behind_cursor = {
            **_row(0),
            "id": cursor_id[:-1] + "-",  # '-' sorts below any digit -> < cursor_id
        }
        new_row_ahead_of_cursor = _row(999_999)  # id sorts well after every existing row

        def patched_execute(self: _FakeQueryBuilder) -> SimpleNamespace:
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Concurrent insert lands "behind" what page 1 already
                # returned (an id sorting well before the cursor) -
                # exactly the kind of write that would shift every later
                # row's offset under .range()-based pagination.
                client.rows.insert(250, new_row_behind_cursor)
                # And a second concurrent insert lands "ahead" of the
                # cursor - a genuinely new row that page 2 must find.
                client.rows.append(new_row_ahead_of_cursor)
            return real_execute(self)

        with patch.object(_FakeQueryBuilder, "execute", patched_execute):
            events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        ids = [e.calendar_event_id for e in events]
        self.assertEqual(len(ids), len(set(ids)), "duplicate rows across the concurrent insert")

        original_ids = {row["id"] for row in rows}
        returned_ids = set(ids)
        # Every row that existed before pagination even started - both
        # what page 1 already returned AND what was still ahead of the
        # cursor for page 2 to find - must still all be present. None of
        # them may be skipped just because an unrelated row was inserted
        # behind the cursor in between the two page fetches.
        self.assertTrue(
            original_ids.issubset(returned_ids), "a pre-existing row was skipped"
        )
        # The row inserted ahead of the cursor is a genuinely new row
        # within [from_date, to_date] and must be picked up, exactly once.
        self.assertEqual(ids.count(new_row_ahead_of_cursor["id"]), 1)

        # Deterministic termination: exactly 2 requests (page 1 full,
        # page 2 short), regardless of the concurrent writes in between.
        self.assertEqual(len(client.builders), 2)


if __name__ == "__main__":
    unittest.main()
