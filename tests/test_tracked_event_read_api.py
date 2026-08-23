from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    SupabaseTrackedEventRepository,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


class _TrackedEventRepository:
    def __init__(self, events=(), error: str | None = None) -> None:
        self.events = tuple(events)
        self.error = error
        self.active_calls: list[tuple[int, datetime]] = []
        self.history_calls: list[tuple[int, datetime]] = []

    def list_active(self, *, limit: int = 20, now: datetime):
        self.active_calls.append((limit, now))
        if self.error is not None:
            raise RuntimeError(self.error)
        return self.events[:limit]

    def list_history(self, *, limit: int = 20, now: datetime):
        self.history_calls.append((limit, now))
        if self.error is not None:
            raise RuntimeError(self.error)
        return self.events[:limit]


def _event() -> PersistentTrackedEvent:
    event_at = datetime(2026, 8, 23, 23, 30, tzinfo=UTC)
    return PersistentTrackedEvent(
        event_id="352e828c-88f8-4d1b-8ee7-effb11436fe4",
        tracked_instrument_id="tracked-nhf",
        calendar_event_id=None,
        company_name="nib holdings limited",
        instrument="NHF.ASX",
        market="Sydney",
        source="manual",
        external_key="nhf-fy26-2026-08-24",
        kind="earnings",
        title="FY26 results",
        event_at=event_at,
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=TrackedEventStatus.TRACKED,
        resolved_etoro_instrument_id=3343,
        resolved_etoro_symbol="NHF.ASX",
        resolved_etoro_display_name="nib holdings limited",
        resolution_armed_at=datetime(2026, 8, 23, 14, 0, tzinfo=UTC),
        resolution_armed_by="tracked-event-preflight",
        reference_price=Decimal("6.24"),
        reference_captured_at=datetime(2026, 8, 23, 23, 29, 45, tzinfo=UTC),
        reference_kind="etoro_last_execution_pre_event_snapshot",
        reaction_anchor_at=None,
        last_error=None,
        updated_at=datetime(2026, 8, 23, 23, 29, 45, tzinfo=UTC),
    )


class TrackedEventReadApiTests(unittest.TestCase):
    def test_requires_existing_read_key(self):
        repository = _TrackedEventRepository((_event(),))
        client = TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

        response = client.get("/api/v1/tracked-events")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(repository.active_calls, [])
        self.assertEqual(repository.history_calls, [])

    def test_default_view_is_active_and_returns_observation_only_runtime_state(self):
        repository = _TrackedEventRepository((_event(),))
        client = TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

        response = client.get(
            "/api/v1/tracked-events?limit=7",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([limit for limit, _ in repository.active_calls], [7])
        self.assertEqual(repository.history_calls, [])
        self.assertEqual(
            response.json(),
            [
                {
                    "event_id": "352e828c-88f8-4d1b-8ee7-effb11436fe4",
                    "tracked_instrument_id": "tracked-nhf",
                    "calendar_event_id": None,
                    "company_name": "nib holdings limited",
                    "instrument": "NHF.ASX",
                    "market": "Sydney",
                    "source": "manual",
                    "external_key": "nhf-fy26-2026-08-24",
                    "kind": "earnings",
                    "title": "FY26 results",
                    "event_at": "2026-08-23T23:30:00+00:00",
                    "event_time_status": "estimated",
                    "status": "tracked",
                    "resolved_etoro_instrument_id": 3343,
                    "resolved_etoro_symbol": "NHF.ASX",
                    "resolved_etoro_display_name": "nib holdings limited",
                    "resolution_armed_at": "2026-08-23T14:00:00+00:00",
                    "resolution_armed_by": "tracked-event-preflight",
                    "reference_price": "6.24",
                    "reference_captured_at": "2026-08-23T23:29:45+00:00",
                    "reference_kind": "etoro_last_execution_pre_event_snapshot",
                    "reaction_anchor_at": None,
                    "started_at": None,
                    "completed_at": None,
                    "last_error": None,
                    "updated_at": "2026-08-23T23:29:45+00:00",
                }
            ],
        )

    def test_view_history_uses_history_view(self):
        repository = _TrackedEventRepository((_event(),))
        client = TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

        response = client.get(
            "/api/v1/tracked-events?view=history&limit=13",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repository.active_calls, [])
        self.assertEqual([limit for limit, _ in repository.history_calls], [13])

    def test_invalid_view_is_rejected_without_calling_repository(self):
        repository = _TrackedEventRepository((_event(),))
        client = TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

        response = client.get(
            "/api/v1/tracked-events?view=bogus",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(repository.active_calls, [])
        self.assertEqual(repository.history_calls, [])

    def test_repository_failure_is_503(self):
        repository = _TrackedEventRepository(error="tracked-event read failed")
        client = TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

        response = client.get(
            "/api/v1/tracked-events",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "tracked-event read failed")

    def test_supabase_query_failure_is_normalized_to_503(self):
        repository = SupabaseTrackedEventRepository(
            _Client([], error=Exception("postgrest unavailable"))
        )
        client = TestClient(
            create_app(
                tracked_event_repository=repository,
                read_api_key="read-secret",
            )
        )

        response = client.get(
            "/api/v1/tracked-events",
            headers={"X-MarketAI-Key": "read-secret"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("failed to list tracked market events", response.json()["detail"])
        self.assertIn("postgrest unavailable", response.json()["detail"])

    def test_repository_initialization_failure_is_503_not_500(self):
        # No tracked_event_repository injected, so the endpoint's lazy
        # get_tracked_event_repository() falls through to
        # SupabaseTrackedEventRepository.from_env(), which raises RuntimeError
        # when the Supabase env vars are missing - that init failure must be
        # normalized to 503 exactly like a read failure, never leak out as an
        # unhandled 500.
        original_url = os.environ.pop("MARKETAI_SUPABASE_URL", None)
        original_key = os.environ.pop("MARKETAI_SUPABASE_SECRET_KEY", None)
        try:
            client = TestClient(create_app(read_api_key="read-secret"))
            response = client.get(
                "/api/v1/tracked-events",
                headers={"X-MarketAI-Key": "read-secret"},
            )
        finally:
            if original_url is not None:
                os.environ["MARKETAI_SUPABASE_URL"] = original_url
            if original_key is not None:
                os.environ["MARKETAI_SUPABASE_SECRET_KEY"] = original_key

        self.assertEqual(response.status_code, 503)
        self.assertIn("MARKETAI_SUPABASE_URL", response.json()["detail"])


class _Response:
    def __init__(self, data):
        self.data = data


class _Table:
    """Minimal in-memory stand-in for a Supabase/PostgREST query builder.

    Actually applies in_/gte/lt/order/limit against the shared row set so
    active/history boundary tests can assert on real filtering behavior,
    not just on which builder methods were called.
    """

    def __init__(self, rows, error: Exception | None = None) -> None:
        self.rows = list(rows)
        self.error = error
        self.calls: list[tuple] = []
        self._filtered = list(rows)
        self._order_column: str | None = None
        self._order_desc = False
        self._limit: int | None = None

    def select(self, value):
        self.calls.append(("select", value))
        return self

    def in_(self, column, values):
        values = list(values)
        self.calls.append(("in_", column, tuple(values)))
        self._filtered = [row for row in self._filtered if row.get(column) in values]
        return self

    def gte(self, column, value):
        self.calls.append(("gte", column, value))
        self._filtered = [
            row
            for row in self._filtered
            if row.get(column) is not None and row[column] >= value
        ]
        return self

    def lt(self, column, value):
        self.calls.append(("lt", column, value))
        self._filtered = [
            row
            for row in self._filtered
            if row.get(column) is not None and row[column] < value
        ]
        return self

    def order(self, value, **kwargs):
        self.calls.append(("order", value, kwargs))
        self._order_column = value
        self._order_desc = bool(kwargs.get("desc", False))
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        self._limit = value
        return self

    def execute(self):
        self.calls.append(("execute",))
        if self.error is not None:
            raise self.error
        rows = list(self._filtered)
        if self._order_column is not None:
            rows.sort(key=lambda row: row.get(self._order_column) or "", reverse=self._order_desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Response(rows)


class _Client:
    def __init__(self, rows, error: Exception | None = None) -> None:
        self.rows = list(rows)
        self.error = error
        self.table_names: list[str] = []
        self.tables_created: list[_Table] = []

    @property
    def table_query(self) -> _Table:
        return self.tables_created[-1]

    def table(self, name):
        self.table_names.append(name)
        table = _Table(self.rows, error=self.error)
        self.tables_created.append(table)
        return table


def _row(
    *,
    event_id: str,
    status: TrackedEventStatus,
    event_at: datetime,
    updated_at: datetime,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "tracked_instrument_id": f"tracked-{event_id}",
        "calendar_event_id": None,
        "company_name": "acme",
        "instrument": "ACME",
        "market": "Test",
        "source": "manual",
        "external_key": f"key-{event_id}",
        "kind": "earnings",
        "title": "Q1 results",
        "event_at": event_at.isoformat(),
        "event_time_status": TrackedEventTimeStatus.CONFIRMED.value,
        "status": status.value,
        "updated_at": updated_at.isoformat(),
    }


class TrackedEventRecentRepositoryTests(unittest.TestCase):
    def test_list_recent_is_bounded_and_newest_first(self):
        event = _event()
        client = _Client(
            [
                {
                    "id": event.event_id,
                    "tracked_instrument_id": event.tracked_instrument_id,
                    "calendar_event_id": None,
                    "company_name": event.company_name,
                    "instrument": event.instrument,
                    "market": event.market,
                    "source": event.source,
                    "external_key": event.external_key,
                    "kind": event.kind,
                    "title": event.title,
                    "event_at": event.event_at.isoformat(),
                    "event_time_status": event.event_time_status.value,
                    "status": event.status.value,
                }
            ]
        )
        repository = SupabaseTrackedEventRepository(client)

        rows = repository.list_recent(limit=7)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].event_id, event.event_id)
        self.assertEqual(client.table_names, ["tracked_market_events"])
        self.assertEqual(
            client.table_query.calls,
            [
                ("select", "*"),
                ("order", "event_at", {"desc": True}),
                ("limit", 7),
                ("execute",),
            ],
        )

    def test_list_recent_rejects_unbounded_limits(self):
        repository = SupabaseTrackedEventRepository(_Client([]))

        with self.assertRaises(ValueError):
            repository.list_recent(limit=0)
        with self.assertRaises(ValueError):
            repository.list_recent(limit=101)

    def test_list_recent_normalizes_malformed_persisted_row(self):
        event = _event()
        repository = SupabaseTrackedEventRepository(
            _Client(
                [
                    {
                        "id": event.event_id,
                        "tracked_instrument_id": event.tracked_instrument_id,
                        "calendar_event_id": None,
                        "company_name": event.company_name,
                        "instrument": event.instrument,
                        "market": event.market,
                        "source": event.source,
                        "external_key": event.external_key,
                        "kind": event.kind,
                        "title": event.title,
                        "event_at": event.event_at.isoformat(),
                        "event_time_status": "not-a-valid-status",
                        "status": event.status.value,
                    }
                ]
            )
        )

        with self.assertRaisesRegex(RuntimeError, "failed to list tracked market events"):
            repository.list_recent()


class TrackedEventActiveHistoryRepositoryTests(unittest.TestCase):
    def test_tracked_and_monitoring_are_always_in_active_query(self):
        now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
        long_ago = now - timedelta(days=30)
        rows = [
            _row(event_id="a-tracked", status=TrackedEventStatus.TRACKED, event_at=now, updated_at=long_ago),
            _row(event_id="b-monitoring", status=TrackedEventStatus.MONITORING, event_at=now, updated_at=long_ago),
        ]
        repository = SupabaseTrackedEventRepository(_Client(rows))

        events = repository.list_active(limit=20, now=now)

        self.assertEqual({event.event_id for event in events}, {"a-tracked", "b-monitoring"})

    def test_terminal_event_at_or_after_cutoff_is_active(self):
        now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
        just_within_window = now - timedelta(hours=24) + timedelta(seconds=1)
        rows = [
            _row(
                event_id="recent-completed",
                status=TrackedEventStatus.COMPLETED,
                event_at=now,
                updated_at=just_within_window,
            )
        ]
        repository = SupabaseTrackedEventRepository(_Client(rows))

        active_events = repository.list_active(limit=20, now=now)
        history_events = repository.list_history(limit=20, now=now)

        self.assertEqual([event.event_id for event in active_events], ["recent-completed"])
        self.assertEqual(history_events, ())

    def test_terminal_event_before_cutoff_is_history(self):
        now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
        just_past_window = now - timedelta(hours=24) - timedelta(seconds=1)
        rows = [
            _row(
                event_id="stale-failed",
                status=TrackedEventStatus.FAILED,
                event_at=now,
                updated_at=just_past_window,
            )
        ]
        repository = SupabaseTrackedEventRepository(_Client(rows))

        active_events = repository.list_active(limit=20, now=now)
        history_events = repository.list_history(limit=20, now=now)

        self.assertEqual(active_events, ())
        self.assertEqual([event.event_id for event in history_events], ["stale-failed"])

    def test_terminal_event_exactly_at_cutoff_boundary_is_active(self):
        now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
        exactly_at_cutoff = now - timedelta(hours=24)
        rows = [
            _row(
                event_id="boundary-cancelled",
                status=TrackedEventStatus.CANCELLED,
                event_at=now,
                updated_at=exactly_at_cutoff,
            )
        ]
        repository = SupabaseTrackedEventRepository(_Client(rows))

        active_events = repository.list_active(limit=20, now=now)
        history_events = repository.list_history(limit=20, now=now)

        self.assertEqual([event.event_id for event in active_events], ["boundary-cancelled"])
        self.assertEqual(history_events, ())

    def test_terminal_preselection_does_not_lose_newest_event_at_when_over_limit(self):
        # All three rows are terminal and within the 24h active window, but
        # their updated_at order is the reverse of their event_at order.
        # Preselecting the bounded terminal query by updated_at (the old
        # bug) would keep "a" and "b" and drop "c" - even though "c" has the
        # newest event_at and belongs in the final event_at top-2.
        now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
        rows = [
            _row(
                event_id="a-old-event-recent-update",
                status=TrackedEventStatus.COMPLETED,
                event_at=now - timedelta(hours=10),
                updated_at=now - timedelta(hours=1),
            ),
            _row(
                event_id="b-middle",
                status=TrackedEventStatus.COMPLETED,
                event_at=now - timedelta(hours=5),
                updated_at=now - timedelta(hours=2),
            ),
            _row(
                event_id="c-new-event-stale-update",
                status=TrackedEventStatus.COMPLETED,
                event_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=3),
            ),
        ]
        client = _Client(rows)
        repository = SupabaseTrackedEventRepository(client)

        events = repository.list_active(limit=2, now=now)

        self.assertEqual(
            [event.event_id for event in events],
            ["c-new-event-stale-update", "b-middle"],
        )
        terminal_table = client.tables_created[1]
        self.assertIn(("order", "event_at", {"desc": True}), terminal_table.calls)
        self.assertIn(("limit", 2), terminal_table.calls)

    def test_active_and_history_queries_are_bounded(self):
        now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

        active_client = _Client([])
        SupabaseTrackedEventRepository(active_client).list_active(limit=5, now=now)

        self.assertEqual(len(active_client.tables_created), 2)
        for table in active_client.tables_created:
            self.assertIn(("limit", 5), table.calls)

        history_client = _Client([])
        SupabaseTrackedEventRepository(history_client).list_history(limit=9, now=now)

        self.assertEqual(len(history_client.tables_created), 1)
        self.assertIn(("limit", 9), history_client.tables_created[0].calls)


if __name__ == "__main__":
    unittest.main()
