from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any, Iterable

from trading_system.calendar_provider import CalendarCandidate
from trading_system.calendar_repository import (
    CalendarEvent,
    CalendarEventNotFound,
    CalendarEventStatus,
    CalendarSyncResult,
    InvalidCalendarEventTransition,
)


class SupabaseCalendarEventRepository:
    """CalendarEventRepository backed by Supabase Data API.

    Production code uses a backend-only secret/service-role key, same as
    SupabaseEventExpectationRepository. Expo must never receive that key.
    """

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseCalendarEventRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def get(self, calendar_event_id: str) -> CalendarEvent | None:
        response = (
            self.client.table("calendar_events")
            .select("*")
            .eq("id", calendar_event_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return self._row_to_event(rows[0])

    def list_upcoming(self, from_date: date, to_date: date) -> tuple[CalendarEvent, ...]:
        response = (
            self.client.table("calendar_events")
            .select("*")
            .in_("status", [CalendarEventStatus.CANDIDATE.value, CalendarEventStatus.TRACKED.value])
            .gte("scheduled_date", from_date.isoformat())
            .lte("scheduled_date", to_date.isoformat())
            .order("scheduled_date")
            .execute()
        )
        return tuple(self._row_to_event(row) for row in (response.data or []))

    def _upsert_candidate(self, candidate: CalendarCandidate, *, source: str) -> tuple[CalendarEvent, str]:
        response = self.client.rpc(
            "upsert_calendar_candidate",
            {
                "input_company_name": candidate.company_name,
                "input_instrument": candidate.instrument,
                "input_market": candidate.market,
                "input_event_type": candidate.event_type,
                "input_occurrence_key": candidate.occurrence_key,
                "input_scheduled_date": candidate.scheduled_date.isoformat(),
                "input_source": source,
            },
        ).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("upsert_calendar_candidate returned no rows")
        row = rows[0]
        return self._row_to_event(row, out_prefix=True), str(row["out_action"])

    def sync_candidates(
        self, candidates: Iterable[CalendarCandidate], *, source: str
    ) -> CalendarSyncResult:
        inserted: list[str] = []
        updated: list[str] = []
        skipped_locked: list[str] = []

        for candidate in candidates:
            event, action = self._upsert_candidate(candidate, source=source)
            if action == "inserted":
                inserted.append(event.calendar_event_id)
            elif action == "updated":
                updated.append(event.calendar_event_id)
            else:
                skipped_locked.append(event.calendar_event_id)

        return CalendarSyncResult(
            inserted=tuple(inserted), updated=tuple(updated), skipped_locked=tuple(skipped_locked)
        )

    def add_manual_event(
        self,
        candidate: CalendarCandidate,
        *,
        status: CalendarEventStatus = CalendarEventStatus.CANDIDATE,
    ) -> CalendarEvent:
        event, _action = self._upsert_candidate(candidate, source=candidate.source)
        if status == CalendarEventStatus.TRACKED and event.status == CalendarEventStatus.CANDIDATE:
            return self.track(event.calendar_event_id)
        return event

    def _transition(
        self, calendar_event_id: str, *, from_status: CalendarEventStatus, to_status: CalendarEventStatus
    ) -> CalendarEvent:
        try:
            response = self.client.rpc(
                "transition_calendar_event_status",
                {
                    "input_calendar_event_id": calendar_event_id,
                    "input_from_status": from_status.value,
                    "input_to_status": to_status.value,
                },
            ).execute()
        except Exception as exc:
            if self._is_not_found(exc):
                raise CalendarEventNotFound(calendar_event_id) from exc
            if self._is_invalid_transition(exc):
                raise InvalidCalendarEventTransition(
                    f"cannot transition calendar event {calendar_event_id} from "
                    f"{from_status.value!r} to {to_status.value!r}"
                ) from exc
            raise

        rows = response.data or []
        if not rows:
            raise RuntimeError("transition_calendar_event_status returned no rows")
        return self._row_to_event(rows[0], out_prefix=True)

    def track(self, calendar_event_id: str) -> CalendarEvent:
        return self._transition(
            calendar_event_id,
            from_status=CalendarEventStatus.CANDIDATE,
            to_status=CalendarEventStatus.TRACKED,
        )

    def untrack(self, calendar_event_id: str) -> CalendarEvent:
        return self._transition(
            calendar_event_id,
            from_status=CalendarEventStatus.TRACKED,
            to_status=CalendarEventStatus.CANDIDATE,
        )

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        return "calendar_event_not_found" in str(exc) or getattr(exc, "code", None) == "P0002"

    @staticmethod
    def _is_invalid_transition(exc: Exception) -> bool:
        return "invalid_calendar_event_transition" in str(exc) or getattr(exc, "code", None) == "P0001"

    @classmethod
    def _row_to_event(cls, row: dict[str, Any], *, out_prefix: bool = False) -> CalendarEvent:
        prefix = "out_" if out_prefix else ""
        return CalendarEvent(
            calendar_event_id=str(row[f"{prefix}id"]),
            company_name=str(row[f"{prefix}company_name"]),
            instrument=str(row[f"{prefix}instrument"]),
            market=str(row[f"{prefix}market"]),
            event_type=str(row[f"{prefix}event_type"]),
            scheduled_date=cls._parse_date(row[f"{prefix}scheduled_date"]),
            source=str(row[f"{prefix}source"]),
            occurrence_key=str(row[f"{prefix}occurrence_key"]),
            status=CalendarEventStatus(row[f"{prefix}status"]),
            created_at=cls._parse_datetime(row.get(f"{prefix}created_at")) or datetime.now(UTC),
            updated_at=cls._parse_datetime(row.get(f"{prefix}updated_at")) or datetime.now(UTC),
        )

    @staticmethod
    def _parse_date(value: str | date) -> date:
        return value if isinstance(value, date) else date.fromisoformat(value)

    @staticmethod
    def _parse_datetime(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
