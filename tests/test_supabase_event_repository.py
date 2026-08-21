import unittest
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from trading_system.models import EventExpectation
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository


class _ListUpcomingQuery:
    """Records which filters list_upcoming() applies before calling execute()."""

    def __init__(self, rows: list[dict[str, Any]], calls: list[str]) -> None:
        self.rows = rows
        self.calls = calls

    def select(self, *_args: Any) -> "_ListUpcomingQuery":
        self.calls.append("select")
        return self

    def eq(self, *_args: Any) -> "_ListUpcomingQuery":
        self.calls.append("eq")
        return self

    def gte(self, *_args: Any) -> "_ListUpcomingQuery":
        self.calls.append("gte")
        return self

    def order(self, *_args: Any, **_kwargs: Any) -> "_ListUpcomingQuery":
        self.calls.append("order")
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self.rows)


class _ListUpcomingClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def table(self, _name: str) -> _ListUpcomingQuery:
        return _ListUpcomingQuery(self.rows, self.calls)


class _UpsertQuery:
    def __init__(self) -> None:
        pass

    def upsert(self, _payload: dict[str, Any], on_conflict: str | None = None) -> "_UpsertQuery":
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[])


class _RpcCall:
    def __init__(self, response: Any) -> None:
        self._response = response

    def execute(self) -> Any:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _SaveClient:
    """Fake client for save(): records the market_events upsert and the
    insert_next_expectation_version RPC call separately, since save() now
    talks to both."""

    def __init__(self, rpc_response: Any) -> None:
        self.rpc_response = rpc_response
        self.table_calls: list[str] = []
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> _UpsertQuery:
        self.table_calls.append(name)
        return _UpsertQuery()

    def rpc(self, name: str, params: dict[str, Any]) -> _RpcCall:
        self.rpc_calls.append((name, params))
        return _RpcCall(self.rpc_response)


class SupabaseEventExpectationRepositoryTests(unittest.TestCase):
    def test_row_mapping_preserves_version_and_editable_fields(self) -> None:
        expectation = SupabaseEventExpectationRepository._row_to_expectation(
            {
                "event_id": "hays-fy2026-results",
                "instrument": "HAS.L",
                "event_name": "Hays plc FY2026 results",
                "scheduled_date": "2026-08-20",
                "version": 2,
                "source_name": "Hays plc analyst consensus",
                "source_url": "https://example.invalid/hays",
                "source_as_of": "2026-07-01",
                "consensus": {"fy27_operating_profit_pre_exceptional_gbp_m": 57.0},
                "important_kpis": ["fy27_operating_profit_pre_exceptional_gbp_m"],
                "bull_case": ["FY27 outlook above consensus"],
                "base_case": ["FY27 outlook near consensus"],
                "bear_case": ["FY27 outlook below consensus"],
                "triggers": {"bull_fy27_operating_profit_gbp_m": 62.0},
                "invalidation_conditions": ["conflicting price reaction"],
                "created_at": "2026-08-18T06:30:00+00:00",
            }
        )

        self.assertEqual(expectation.version, 2)
        self.assertEqual(expectation.scheduled_date, date(2026, 8, 20))
        self.assertEqual(expectation.source_as_of, date(2026, 7, 1))
        self.assertEqual(
            expectation.consensus["fy27_operating_profit_pre_exceptional_gbp_m"],
            57.0,
        )
        self.assertEqual(
            expectation.triggers["bull_fy27_operating_profit_gbp_m"], 62.0
        )
        self.assertEqual(
            expectation.updated_at,
            datetime(2026, 8, 18, 6, 30, tzinfo=UTC),
        )

    def test_list_upcoming_returns_released_and_past_dated_events_too(self) -> None:
        # A released event (e.g. yesterday's earnings) must stay reachable
        # through /api/v1/events - its detail page still hosts the paper-run
        # dashboard - so list_upcoming() must not filter by status or date.
        released_row = {
            "event_id": "hays-fy2026-results",
            "instrument": "HAS.L",
            "event_name": "Hays plc FY2026 results",
            "scheduled_date": "2020-01-01",
            "version": 1,
            "created_at": "2020-01-01T00:00:00+00:00",
        }
        client = _ListUpcomingClient([released_row])
        repo = SupabaseEventExpectationRepository(client)

        results = repo.list_upcoming()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].event_id, "hays-fy2026-results")
        self.assertNotIn("eq", client.calls)
        self.assertNotIn("gte", client.calls)
        self.assertIn("order", client.calls)

    def test_list_upcoming_orders_active_events_ahead_of_history(self) -> None:
        # Plain ascending scheduled_date buries today's/upcoming events
        # under accumulating history now that past events are retained
        # (the fix above). Active events (today or later) must sort first,
        # soonest first; already-released events follow, most recently
        # released first - so the home screen's "Seurannassa" list leads
        # with what's current instead of the oldest tracked event.
        today = date.today()

        def row(event_id: str, days_offset: int) -> dict[str, Any]:
            scheduled = today + timedelta(days=days_offset)
            return {
                "event_id": event_id,
                "instrument": "X.L",
                "event_name": event_id,
                "scheduled_date": scheduled.isoformat(),
                "version": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
            }

        rows = [
            row("far-past", -10),
            row("near-past", -2),
            row("far-future", 5),
            row("near-future", 1),
            row("today", 0),
        ]
        client = _ListUpcomingClient(rows)
        repo = SupabaseEventExpectationRepository(client)

        results = [event.event_id for event in repo.list_upcoming()]

        self.assertEqual(
            results, ["today", "near-future", "far-future", "near-past", "far-past"]
        )

    def test_save_upserts_identity_then_calls_the_locked_insert_rpc(self) -> None:
        # save() must go through insert_next_expectation_version() (the
        # pg_advisory_xact_lock-protected RPC shared with
        # approve_strategy_draft()) rather than its own unlocked
        # select-max-then-insert-with-retry - that unlocked version is what
        # let a concurrent admin write and strategy-draft approval race
        # each other's version allocation.
        client = _SaveClient(
            SimpleNamespace(data=[{"new_version": 2, "created_at": "2026-08-18T06:30:00+00:00"}])
        )
        repo = SupabaseEventExpectationRepository(client)
        expectation = EventExpectation(
            event_id="hays-fy2026-results",
            instrument="HAS.L",
            event_name="Hays plc FY2026 results",
            scheduled_date=date(2026, 8, 20),
            consensus={"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
            triggers={"bull_fy27_operating_profit_gbp_m": 60.0},
            version=1,
        )

        saved = repo.save(expectation, change_note="raise bull threshold")

        self.assertEqual(saved.version, 2)
        self.assertEqual(
            saved.updated_at, datetime(2026, 8, 18, 6, 30, tzinfo=UTC)
        )
        self.assertIn("market_events", client.table_calls)
        self.assertEqual(len(client.rpc_calls), 1)
        name, params = client.rpc_calls[0]
        self.assertEqual(name, "insert_next_expectation_version")
        self.assertEqual(params["input_event_id"], "hays-fy2026-results")
        self.assertEqual(params["input_change_note"], "raise bull threshold")
        self.assertEqual(
            params["input_consensus"]["fy27_operating_profit_pre_exceptional_gbp_m"],
            55.6,
        )
        self.assertEqual(
            params["input_triggers"]["bull_fy27_operating_profit_gbp_m"], 60.0
        )

    def test_save_propagates_rpc_errors_without_a_retry_loop(self) -> None:
        # No more client-side retry-on-23505: the database lock is what
        # prevents the race that used to cause those conflicts, so any RPC
        # error (including a lock-protected version conflict) must surface
        # to the caller unchanged, not be silently retried away.
        error = RuntimeError("expectation_version_conflict: expected 1 but current is 2")
        client = _SaveClient(error)
        repo = SupabaseEventExpectationRepository(client)
        expectation = EventExpectation(
            event_id="hays-fy2026-results",
            instrument="HAS.L",
            event_name="Hays plc FY2026 results",
            scheduled_date=date(2026, 8, 20),
            version=1,
        )

        with self.assertRaises(RuntimeError):
            repo.save(expectation, change_note="raise bull threshold")

        self.assertEqual(len(client.rpc_calls), 1)


if __name__ == "__main__":
    unittest.main()
