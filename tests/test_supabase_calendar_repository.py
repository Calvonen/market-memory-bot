"""Exercises SupabaseCalendarEventRepository.list_upcoming()'s pagination
against a fake Supabase Data API client - not source-text assertions.

Regression for three Codex findings:

- P2 (round 6): a single unpaginated request silently truncates at
  PostgREST's default `db-max-rows` (1,000).
- P2 (round 11): offset-based pagination (`.range(offset, ...)`) is not
  safe against concurrent writes - a row landing ahead of the current
  offset boundary shifts every later row's position, so the next
  fixed-offset request either re-returns an already-seen row (duplicate)
  or skips the row that used to sit at that boundary.
- P2 (this round): a `(scheduled_date, id)` keyset cursor is itself not
  safe, because `scheduled_date` is *not* immutable - a still-candidate
  row's date can move on a later sync (see "Idempotent sync" in
  docs/calendar_watchlist.md). If a not-yet-fetched row's date moves to
  sort behind such a cursor, it would be silently skipped forever; if an
  already-fetched row's date moves to sort ahead of it, it could be
  re-fetched as a duplicate. This proves the real pagination loop in
  supabase_calendar_repository.py instead pages on `id` alone - the
  table's immutable primary key - and only sorts the complete,
  already-paginated result set by `(scheduled_date, id)` once, at the
  very end, entirely independent of the pagination walk itself.
"""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from trading_system.calendar_repository import CalendarEventStatus
from trading_system.supabase_calendar_repository import (
    _LIST_UPCOMING_PAGE_SIZE,
    SupabaseCalendarEventRepository,
)


class _FakeQueryBuilder:
    """Mimics just enough of supabase-py's fluent table-query builder for
    list_upcoming(): filter methods actually filter (against the table's
    *live* row list, not a snapshot), so a test can mutate the table in
    between page fetches to simulate a concurrent insert/update - the
    same thing a real concurrent sync or track() call would do against a
    real Postgres table mid-pagination."""

    def __init__(self, table: "_FakeCalendarClient") -> None:
        self._table = table
        self._status_in: list[str] | None = None
        self._gte: str | None = None
        self._lte: str | None = None
        self._gt_id: str | None = None
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
        self._gte = value
        return self

    def lte(self, column: str, value: str) -> "_FakeQueryBuilder":
        assert column == "scheduled_date"
        self._lte = value
        return self

    def gt(self, column: str, value: str) -> "_FakeQueryBuilder":
        assert column == "id", "pagination cursor must be the immutable id column"
        self._gt_id = value
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
        matched.sort(key=lambda row: row["id"])
        return SimpleNamespace(data=matched[: self._limit])

    def _matches(self, row: dict) -> bool:
        if self._status_in is not None and row["status"] not in self._status_in:
            return False
        if self._gte is not None and row["scheduled_date"] < self._gte:
            return False
        if self._lte is not None and row["scheduled_date"] > self._lte:
            return False
        if self._gt_id is not None and not (row["id"] > self._gt_id):
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
        # No numeric offset anywhere - the first page has no cursor at
        # all, and every later page's cursor is the immutable `id` *value*
        # of the previous page's own last row, not a running count.
        self.assertIsNone(client.builders[0]._gt_id)
        for builder in client.builders[1:]:
            self.assertIsNotNone(builder._gt_id)
        self.assertEqual([b._limit for b in client.builders], [_LIST_UPCOMING_PAGE_SIZE] * 3)

    def test_second_page_cursor_is_the_immutable_id_of_the_first_pages_last_row(self) -> None:
        total_rows = _LIST_UPCOMING_PAGE_SIZE + 5
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)

        repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        last_row_of_page_one = rows[_LIST_UPCOMING_PAGE_SIZE - 1]
        self.assertEqual(client.builders[1]._gt_id, last_row_of_page_one["id"])

    def test_pagination_orders_by_id_alone_final_result_sorted_by_date_then_id(self) -> None:
        # The pagination *walk* orders purely by the immutable `id` column
        # - never scheduled_date, which is mutable and therefore unsafe as
        # a cursor (see module docstring). The caller-facing order
        # (upcoming-soonest-first) is applied exactly once, on the
        # complete accumulated result, with `id` only as the tie-break for
        # rows sharing the same date.
        rows = [
            _row(0, scheduled_date="2026-09-05"),
            _row(1, scheduled_date="2026-09-01"),
            _row(2, scheduled_date="2026-09-01"),
            _row(3, scheduled_date="2026-09-03"),
        ]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)

        events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        self.assertEqual(client.builders[0].order_calls, ["id"])
        self.assertEqual(
            [e.calendar_event_id for e in events],
            [rows[1]["id"], rows[2]["id"], rows[3]["id"], rows[0]["id"]],
        )

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

    # -- concurrent change mid-pagination (P2 regressions) -------------------

    def test_concurrent_insert_behind_the_cursor_never_skips_a_remaining_row(self) -> None:
        # The classic offset-pagination bug: a row lands ahead of the
        # current offset boundary between two page fetches, shifting every
        # later row's absolute position by one. Keyset pagination on the
        # immutable `id` has no numeric offset to shift - page 2's cursor
        # is the literal `id` of page 1's actual last row, so a concurrent
        # insert elsewhere in the table can never move it.
        page_size = _LIST_UPCOMING_PAGE_SIZE
        total_rows = page_size + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(list(rows))
        repo = SupabaseCalendarEventRepository(client)

        real_execute = _FakeQueryBuilder.execute
        call_count = {"n": 0}
        cursor_id = f"00000000-0000-0000-0000-{page_size - 1:012d}"
        new_row_behind_cursor = {
            **_row(0),
            "id": cursor_id[:-1] + "-",  # '-' sorts below any digit -> < cursor_id
        }
        new_row_ahead_of_cursor = _row(999_999)  # id sorts well after every existing row

        def patched_execute(self: _FakeQueryBuilder) -> SimpleNamespace:
            call_count["n"] += 1
            if call_count["n"] == 2:
                client.rows.insert(250, new_row_behind_cursor)
                client.rows.append(new_row_ahead_of_cursor)
            return real_execute(self)

        with patch.object(_FakeQueryBuilder, "execute", patched_execute):
            events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        ids = [e.calendar_event_id for e in events]
        self.assertEqual(len(ids), len(set(ids)), "duplicate rows across the concurrent insert")

        original_ids = {row["id"] for row in rows}
        self.assertTrue(original_ids.issubset(set(ids)), "a pre-existing row was skipped")
        self.assertEqual(ids.count(new_row_ahead_of_cursor["id"]), 1)
        self.assertEqual(len(client.builders), 2)

    def test_an_already_read_rows_scheduled_date_moving_later_never_duplicates_it(self) -> None:
        # P2 regression: a (scheduled_date, id) cursor would have let this
        # already-fetched row re-match a later page once its date moved
        # past the cursor's date. Paginating on immutable `id` alone means
        # this row's page 1 membership can never change, no matter what
        # happens to its scheduled_date afterwards.
        page_size = _LIST_UPCOMING_PAGE_SIZE
        total_rows = page_size + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(list(rows))
        repo = SupabaseCalendarEventRepository(client)

        already_read_id = rows[5]["id"]  # will land on page 1 (id < page_size - 1)
        real_execute = _FakeQueryBuilder.execute
        call_count = {"n": 0}

        def patched_execute(self: _FakeQueryBuilder) -> SimpleNamespace:
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Concurrent sync moves an already-returned row's date to
                # much later - still inside [from_date, to_date] - between
                # page 1 and page 2.
                for row in client.rows:
                    if row["id"] == already_read_id:
                        row["scheduled_date"] = "2026-11-20"
            return real_execute(self)

        with patch.object(_FakeQueryBuilder, "execute", patched_execute):
            events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        ids = [e.calendar_event_id for e in events]
        self.assertEqual(len(ids), len(set(ids)), "duplicate rows after a scheduled_date shift")
        self.assertEqual(ids.count(already_read_id), 1)
        self.assertEqual(len(ids), total_rows, "row count changed even though nothing left the date window")

        # The returned event reflects the snapshot page 1 actually
        # observed (its date at that moment), not the later mutation -
        # ordinary read-consistency, not a pagination bug.
        moved_event = next(e for e in events if e.calendar_event_id == already_read_id)
        self.assertEqual(moved_event.scheduled_date.isoformat(), "2026-09-01")

    def test_an_unread_rows_scheduled_date_moving_earlier_never_omits_it(self) -> None:
        # P2 regression: a (scheduled_date, id) cursor would have excluded
        # this not-yet-fetched row from page 2 once its date moved behind
        # the cursor's date, silently dropping it forever. Paginating on
        # immutable `id` alone means this row's page 2 membership was
        # already fixed the moment it was created, unaffected by its date.
        page_size = _LIST_UPCOMING_PAGE_SIZE
        total_rows = page_size + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(list(rows))
        repo = SupabaseCalendarEventRepository(client)

        unread_id = rows[page_size + 3]["id"]  # will land on page 2
        real_execute = _FakeQueryBuilder.execute
        call_count = {"n": 0}

        def patched_execute(self: _FakeQueryBuilder) -> SimpleNamespace:
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Concurrent sync moves a not-yet-fetched row's date
                # earlier - still inside [from_date, to_date] - between
                # page 1 and page 2.
                for row in client.rows:
                    if row["id"] == unread_id:
                        row["scheduled_date"] = "2026-01-15"
            return real_execute(self)

        with patch.object(_FakeQueryBuilder, "execute", patched_execute):
            events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        ids = [e.calendar_event_id for e in events]
        self.assertEqual(len(ids), len(set(ids)), "duplicate rows after a scheduled_date shift")
        self.assertIn(unread_id, ids, "row was silently dropped after its date moved earlier")
        self.assertEqual(len(ids), total_rows, "row count changed even though nothing left the date window")


class SupabaseCalendarRepositoryUuidNormalizationTests(unittest.TestCase):
    def test_track_and_untrack_send_canonical_uuid_text_to_rpc(self) -> None:
        class RpcClient:
            def __init__(self) -> None:
                self.payloads: list[dict[str, str]] = []

            def rpc(self, name: str, payload: dict[str, str]):
                self.assert_rpc_name = name
                self.payloads.append(payload)
                return self

            def execute(self) -> SimpleNamespace:
                return SimpleNamespace(data=[])

        dashless = "550e8400e29b41d4a716446655440000"
        canonical = "550e8400-e29b-41d4-a716-446655440000"
        client = RpcClient()
        repo = SupabaseCalendarEventRepository(client)

        for transition in (repo.track, repo.untrack):
            with self.subTest(transition=transition.__name__):
                with self.assertRaises(RuntimeError):
                    transition(dashless)

        self.assertEqual(client.assert_rpc_name, "transition_calendar_event_status")
        self.assertEqual(
            [payload["input_calendar_event_id"] for payload in client.payloads],
            [canonical, canonical],
        )


if __name__ == "__main__":
    unittest.main()
