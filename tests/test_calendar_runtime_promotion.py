from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

from trading_system.calendar_repository import CalendarEvent, CalendarEventStatus
from trading_system.calendar_runtime_promotion import SupabaseCalendarRuntimePromotionRepository
from trading_system.calendar_runtime_timing import CalendarRuntimeTiming
from trading_system.tracked_event_repository import TrackedEventTimeStatus


MIGRATION = Path(
    "supabase/migrations/20260902100000_calendar_runtime_promotion_atomic.sql"
)


class _RpcCall:
    def __init__(self, response) -> None:
        self.response = response

    def execute(self):
        return self.response


class _Client:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, params: dict):
        self.calls.append((name, params))
        return _RpcCall(self.response)


class CalendarRuntimePromotionRepositoryTests(unittest.TestCase):
    def test_promote_passes_exact_calendar_identity_and_resolved_timing(self) -> None:
        client = _Client(
            SimpleNamespace(
                data=[
                    {
                        "out_event_id": "22222222-2222-2222-2222-222222222222",
                        "out_action": "inserted",
                        "out_calendar_status": "tracked",
                    }
                ]
            )
        )
        repository = SupabaseCalendarRuntimePromotionRepository(client)
        event = CalendarEvent(
            calendar_event_id="11111111-1111-1111-1111-111111111111",
            company_name="DICK'S SPORTING GOODS INC",
            instrument="DKS",
            market="USA",
            event_type="earnings",
            scheduled_date=date(2026, 8, 25),
            source="finnhub",
            occurrence_key="2027Q2",
            status=CalendarEventStatus.CANDIDATE,
        )
        timing = CalendarRuntimeTiming(
            event_at=datetime(2026, 8, 25, 13, 30, tzinfo=UTC),
            event_time_status=TrackedEventTimeStatus.ESTIMATED,
            provider_timing="bmo",
        )

        result = repository.promote(event, timing, actor="calendar-track-api")

        self.assertEqual(result.event_id, "22222222-2222-2222-2222-222222222222")
        self.assertEqual(result.action, "inserted")
        self.assertEqual(result.calendar_status, "tracked")
        self.assertEqual(
            client.calls,
            [
                (
                    "promote_calendar_event_to_tracked_runtime",
                    {
                        "input_calendar_event_id": event.calendar_event_id,
                        "input_expected_instrument": "DKS",
                        "input_expected_event_type": "earnings",
                        "input_expected_source": "finnhub",
                        "input_expected_occurrence_key": "2027Q2",
                        "input_expected_scheduled_date": "2026-08-25",
                        "input_event_at": "2026-08-25T13:30:00+00:00",
                        "input_event_time_status": "estimated",
                        "input_actor": "calendar-track-api",
                    },
                )
            ],
        )

    def test_promote_fails_when_rpc_returns_no_row(self) -> None:
        repository = SupabaseCalendarRuntimePromotionRepository(
            _Client(SimpleNamespace(data=[]))
        )
        event = CalendarEvent(
            calendar_event_id="11111111-1111-1111-1111-111111111111",
            company_name="DICK'S SPORTING GOODS INC",
            instrument="DKS",
            market="USA",
            event_type="earnings",
            scheduled_date=date(2026, 8, 25),
            source="finnhub",
            occurrence_key="2027Q2",
        )
        timing = CalendarRuntimeTiming(
            event_at=datetime(2026, 8, 25, 13, 30, tzinfo=UTC),
            event_time_status=TrackedEventTimeStatus.ESTIMATED,
            provider_timing="bmo",
        )

        with self.assertRaisesRegex(RuntimeError, "returned no rows"):
            repository.promote(event, timing, actor="calendar-track-api")


class CalendarRuntimePromotionMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MIGRATION.read_text(encoding="utf-8")
        cls.lower = cls.source.lower()

    def test_promotion_locks_calendar_before_runtime_upsert(self) -> None:
        lock_index = self.lower.index("from public.calendar_events")
        lock_clause_index = self.lower.index("for update", lock_index)
        upsert_index = self.lower.index("from public.upsert_tracked_market_event", lock_clause_index)
        self.assertLess(lock_clause_index, upsert_index)

    def test_promotion_revalidates_identity_and_date_before_runtime_upsert(self) -> None:
        stale_guard = self.lower.index("calendar_event_changed_before_promotion")
        upsert_index = self.lower.index("from public.upsert_tracked_market_event")
        self.assertLess(stale_guard, upsert_index)
        for field in (
            "calendar_row.instrument is distinct from input_expected_instrument",
            "calendar_row.event_type is distinct from input_expected_event_type",
            "calendar_row.source is distinct from input_expected_source",
            "calendar_row.occurrence_key is distinct from input_expected_occurrence_key",
            "calendar_row.scheduled_date is distinct from input_expected_scheduled_date",
        ):
            self.assertIn(field, self.source)

    def test_calendar_status_changes_only_after_runtime_upsert(self) -> None:
        upsert_index = self.lower.index("from public.upsert_tracked_market_event")
        status_update_index = self.lower.index("update public.calendar_events", upsert_index)
        self.assertLess(upsert_index, status_update_index)

    def test_promotion_is_idempotent_for_candidate_or_tracked_watchlist_state(self) -> None:
        self.assertIn("calendar_row.status not in ('candidate', 'tracked')", self.source)
        self.assertIn("if calendar_row.status = 'candidate' then", self.source)
        self.assertIn("'calendar:' || calendar_row.id::text", self.source)

    def test_schema_gate_requires_promotion_rpc_and_runtime_version_8(self) -> None:
        self.assertIn(
            "promote_calendar_event_to_tracked_runtime_function_exists boolean",
            self.source,
        )
        self.assertIn(
            "public.promote_calendar_event_to_tracked_runtime(uuid,text,text,text,text,date,timestamptz,text,text)",
            self.source,
        )
        self.assertIn("select 8;", self.source)

    def test_migration_is_one_transaction(self) -> None:
        stripped = "\n".join(
            line for line in self.source.splitlines() if not line.strip().startswith("--")
        ).strip().lower()
        self.assertTrue(stripped.startswith("begin;"))
        self.assertTrue(stripped.endswith("commit;"))


if __name__ == "__main__":
    unittest.main()
