import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

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

    def test_only_postgres_unique_violation_is_retryable(self) -> None:
        class UniqueError(Exception):
            code = "23505"

        class PermissionError(Exception):
            code = "42501"

        self.assertTrue(
            SupabaseEventExpectationRepository._is_unique_violation(UniqueError())
        )
        self.assertFalse(
            SupabaseEventExpectationRepository._is_unique_violation(PermissionError())
        )


if __name__ == "__main__":
    unittest.main()
