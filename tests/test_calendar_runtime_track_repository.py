from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from trading_system.calendar_runtime_timing import CalendarRuntimeTiming
from trading_system.supabase_calendar_repository import SupabaseCalendarEventRepository
from trading_system.tracked_event_repository import TrackedEventTimeStatus


CALENDAR_ID = "11111111-1111-1111-1111-111111111111"


def _calendar_row(*, status: str) -> dict:
    return {
        "id": CALENDAR_ID,
        "company_name": "DICK'S SPORTING GOODS INC",
        "instrument": "DKS",
        "market": "USA",
        "event_type": "earnings",
        "scheduled_date": "2026-08-25",
        "source": "finnhub",
        "occurrence_key": "2027Q2",
        "status": status,
        "created_at": "2026-08-24T10:00:00+00:00",
        "updated_at": "2026-08-25T10:00:00+00:00",
    }


class _Query:
    def __init__(self, client, table: str) -> None:
        self.client = client
        self.table = table

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        self.client.table_executes.append(self.table)
        rows = self.client.rows_by_table[self.table]
        if callable(rows):
            rows = rows()
        return SimpleNamespace(data=rows)


class _Client:
    def __init__(self, rows_by_table) -> None:
        self.rows_by_table = rows_by_table
        self.table_executes: list[str] = []

    def table(self, name: str):
        return _Query(self, name)


class _Resolver:
    def __init__(self, timing=None, exc: Exception | None = None) -> None:
        self.timing = timing
        self.exc = exc
        self.calls = []

    def resolve(self, event):
        self.calls.append(event)
        if self.exc is not None:
            raise self.exc
        return self.timing


class _PromotionRepository:
    def __init__(self, after_call=None) -> None:
        self.calls = []
        self.after_call = after_call

    def promote(self, event, timing, *, actor: str):
        self.calls.append((event, timing, actor))
        if self.after_call is not None:
            self.after_call()
        return SimpleNamespace(event_id="22222222-2222-2222-2222-222222222222")


class CalendarRuntimeTrackRepositoryTests(unittest.TestCase):
    def test_first_track_resolves_timing_then_promotes_and_returns_tracked_row(self) -> None:
        current_status = {"value": "candidate"}
        client = _Client(
            {
                "calendar_events": lambda: [_calendar_row(status=current_status["value"])],
                "tracked_market_events": [],
            }
        )
        timing = CalendarRuntimeTiming(
            event_at=datetime(2026, 8, 25, 13, 30, tzinfo=UTC),
            event_time_status=TrackedEventTimeStatus.ESTIMATED,
            provider_timing="bmo",
        )
        resolver = _Resolver(timing=timing)
        promotion = _PromotionRepository(
            after_call=lambda: current_status.__setitem__("value", "tracked")
        )
        repository = SupabaseCalendarEventRepository(
            client,
            runtime_timing_resolver=resolver,
            runtime_promotion_repository=promotion,
        )

        saved = repository.track(CALENDAR_ID)

        self.assertEqual(saved.status.value, "tracked")
        self.assertEqual(len(resolver.calls), 1)
        self.assertEqual(len(promotion.calls), 1)
        self.assertEqual(promotion.calls[0][1], timing)
        self.assertEqual(promotion.calls[0][2], "calendar-track-api")
        self.assertEqual(
            client.table_executes,
            ["calendar_events", "tracked_market_events", "calendar_events"],
        )

    def test_retry_reuses_persisted_runtime_timing_without_calling_finnhub_resolver(self) -> None:
        client = _Client(
            {
                "calendar_events": [_calendar_row(status="tracked")],
                "tracked_market_events": [
                    {
                        "instrument": "DKS",
                        "kind": "earnings",
                        "source": "finnhub",
                        "external_key": f"calendar:{CALENDAR_ID}",
                        "event_at": "2026-08-25T13:30:00+00:00",
                        "event_time_status": "estimated",
                    }
                ],
            }
        )
        resolver = _Resolver(exc=AssertionError("resolver must not run on canonical retry"))
        promotion = _PromotionRepository()
        repository = SupabaseCalendarEventRepository(
            client,
            runtime_timing_resolver=resolver,
            runtime_promotion_repository=promotion,
        )

        saved = repository.track(CALENDAR_ID)

        self.assertEqual(saved.status.value, "tracked")
        self.assertEqual(resolver.calls, [])
        self.assertEqual(len(promotion.calls), 1)
        self.assertEqual(
            promotion.calls[0][1].event_at,
            datetime(2026, 8, 25, 13, 30, tzinfo=UTC),
        )
        self.assertEqual(promotion.calls[0][1].provider_timing, "persisted")

    def test_timing_failure_does_not_call_promotion_write(self) -> None:
        client = _Client(
            {
                "calendar_events": [_calendar_row(status="candidate")],
                "tracked_market_events": [],
            }
        )
        resolver = _Resolver(exc=RuntimeError("timing unavailable"))
        promotion = _PromotionRepository()
        repository = SupabaseCalendarEventRepository(
            client,
            runtime_timing_resolver=resolver,
            runtime_promotion_repository=promotion,
        )

        with self.assertRaisesRegex(RuntimeError, "timing unavailable"):
            repository.track(CALENDAR_ID)

        self.assertEqual(promotion.calls, [])


if __name__ == "__main__":
    unittest.main()
