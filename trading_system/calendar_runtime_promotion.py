from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from trading_system.calendar_repository import CalendarEvent
from trading_system.calendar_runtime_timing import CalendarRuntimeTiming


@dataclass(frozen=True)
class CalendarRuntimePromotionResult:
    event_id: str
    action: str
    calendar_status: str


class SupabaseCalendarRuntimePromotionRepository:
    """Atomic calendar -> persistent tracked-event promotion boundary."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseCalendarRuntimePromotionRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def promote(
        self,
        event: CalendarEvent,
        timing: CalendarRuntimeTiming,
        *,
        actor: str,
    ) -> CalendarRuntimePromotionResult:
        response = self.client.rpc(
            "promote_calendar_event_to_tracked_runtime",
            {
                "input_calendar_event_id": event.calendar_event_id,
                "input_expected_instrument": event.instrument,
                "input_expected_event_type": event.event_type,
                "input_expected_source": event.source,
                "input_expected_occurrence_key": event.occurrence_key,
                "input_expected_scheduled_date": event.scheduled_date.isoformat(),
                "input_event_at": timing.event_at.isoformat(),
                "input_event_time_status": timing.event_time_status.value,
                "input_actor": actor,
            },
        ).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("promote_calendar_event_to_tracked_runtime returned no rows")
        row = rows[0]
        return CalendarRuntimePromotionResult(
            event_id=str(row["out_event_id"]),
            action=str(row["out_action"]),
            calendar_status=str(row["out_calendar_status"]),
        )
