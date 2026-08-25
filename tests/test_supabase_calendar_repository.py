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
        self.rows = rows
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
        total_rows = _LIST_UPCOMING_PAGE_SIZE * 2 + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(rows)
        repo = SupabaseCalendarEventRepository(client)
        repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(len(client.builders), 3)
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

    def test_concurrent_insert_behind_the_cursor_never_skips_a_remaining_row(self) -> None:
        page_size = _LIST_UPCOMING_PAGE_SIZE
        total_rows = page_size + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(list(rows))
        repo = SupabaseCalendarEventRepository(client)
        real_execute = _FakeQueryBuilder.execute
        call_count = {"n": 0}
        cursor_id = f"00000000-0000-0000-0000-{page_size - 1:012d}"
        new_row_behind_cursor = {**_row(0), "id": cursor_id[:-1] + "-"}
        new_row_ahead_of_cursor = _row(999_999)

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
        page_size = _LIST_UPCOMING_PAGE_SIZE
        total_rows = page_size + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(list(rows))
        repo = SupabaseCalendarEventRepository(client)
        already_read_id = rows[5]["id"]
        real_execute = _FakeQueryBuilder.execute
        call_count = {"n": 0}

        def patched_execute(self: _FakeQueryBuilder) -> SimpleNamespace:
            call_count["n"] += 1
            if call_count["n"] == 2:
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
        moved_event = next(e for e in events if e.calendar_event_id == already_read_id)
        self.assertEqual(moved_event.scheduled_date.isoformat(), "2026-09-01")

    def test_an_unread_rows_scheduled_date_moving_earlier_never_omits_it(self) -> None:
        page_size = _LIST_UPCOMING_PAGE_SIZE
        total_rows = page_size + 10
        rows = [_row(i) for i in range(total_rows)]
        client = _FakeCalendarClient(list(rows))
        repo = SupabaseCalendarEventRepository(client)
        unread_id = rows[page_size + 3]["id"]
        real_execute = _FakeQueryBuilder.execute
        call_count = {"n": 0}

        def patched_execute(self: _FakeQueryBuilder) -> SimpleNamespace:
            call_count["n"] += 1
            if call_count["n"] == 2:
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
    def test_track_and_untrack_use_canonical_uuid_through_current_runtime_contract(self) -> None:
        dashless = "550e8400e29b41d4a716446655440000"
        canonical = "550e8400-e29b-41d4-a716-446655440000"

        def calendar_row(status: str) -> dict:
            return {
                "id": canonical,
                "company_name": "DICK'S SPORTING GOODS INC",
                "instrument": "DKS",
                "market": "USA",
                "event_type": "earnings",
                "occurrence_key": "2027Q2",
                "scheduled_date": "2026-08-25",
                "source": "finnhub",
                "status": status,
                "created_at": "2026-08-24T10:00:00+00:00",
                "updated_at": "2026-08-25T10:00:00+00:00",
            }

        class Query:
            def __init__(self, client, table_name: str) -> None:
                self.client = client
                self.table_name = table_name
                self.filters: list[tuple[str, str]] = []

            def select(self, *_args, **_kwargs):
                return self

            def eq(self, column: str, value: str):
                self.filters.append((column, value))
                self.client.filters.append((self.table_name, column, value))
                return self

            def limit(self, *_args, **_kwargs):
                return self

            def execute(self) -> SimpleNamespace:
                if self.table_name == "tracked_market_events":
                    return SimpleNamespace(data=[])
                self.client.calendar_reads += 1
                status = "candidate" if self.client.calendar_reads == 1 else "tracked"
                return SimpleNamespace(data=[calendar_row(status)])

        class Client:
            def __init__(self) -> None:
                self.filters: list[tuple[str, str, str]] = []
                self.payloads: list[tuple[str, dict[str, str]]] = []
                self.calendar_reads = 0

            def table(self, name: str):
                return Query(self, name)

            def rpc(self, name: str, payload: dict[str, str]):
                self.payloads.append((name, payload))
                return self

            def execute(self) -> SimpleNamespace:
                return SimpleNamespace(data=[])

        class Resolver:
            def resolve(self, event):
                self.event_id = event.calendar_event_id
                return SimpleNamespace(
                    event_at=datetime(2026, 8, 25, 13, 30, tzinfo=UTC),
                    event_time_status=SimpleNamespace(value="estimated"),
                    provider_timing="bmo",
                )

        class Promotion:
            def promote(self, event, timing, *, actor: str):
                self.event_id = event.calendar_event_id
                self.actor = actor
                self.timing = timing
                return SimpleNamespace(event_id="22222222-2222-2222-2222-222222222222")

        client = Client()
        resolver = Resolver()
        promotion = Promotion()
        repo = SupabaseCalendarEventRepository(
            client,
            runtime_timing_resolver=resolver,
            runtime_promotion_repository=promotion,
        )

        tracked = repo.track(dashless)
        self.assertEqual(tracked.status, CalendarEventStatus.TRACKED)
        self.assertEqual(resolver.event_id, canonical)
        self.assertEqual(promotion.event_id, canonical)
        self.assertEqual(promotion.actor, "calendar-track-api")
        self.assertEqual(
            [value for table, column, value in client.filters if table == "calendar_events" and column == "id"],
            [canonical, canonical],
        )
        self.assertEqual(
            [value for table, column, value in client.filters if table == "tracked_market_events" and column == "calendar_event_id"],
            [canonical],
        )

        with self.assertRaises(RuntimeError):
            repo.untrack(dashless)
        self.assertEqual(client.payloads[-1][0], "transition_calendar_event_status")
        self.assertEqual(client.payloads[-1][1]["input_calendar_event_id"], canonical)


if __name__ == "__main__":
    unittest.main()
