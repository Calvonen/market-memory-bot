from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from trading_system.event_repository import EventExpectationRepository
from trading_system.models import EventExpectation


class SupabaseEventExpectationRepository(EventExpectationRepository):
    """Versioned EventExpectation storage backed by Supabase Data API.

    Production code uses a backend-only secret/service-role key. Expo must never
    receive that key directly.
    """

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseEventExpectationRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def get(self, event_id: str) -> EventExpectation | None:
        response = (
            self.client.table("current_event_expectations")
            .select("*")
            .eq("event_id", event_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return self._row_to_expectation(rows[0])

    def list_upcoming(self) -> tuple[EventExpectation, ...]:
        # Every tracked event is returned regardless of date or status: the
        # mobile app's "Seurannassa" list and an event's detail/paper-status
        # view must stay reachable after its scheduled_date has passed, not
        # just while it is still in the future.
        response = (
            self.client.table("current_event_expectations")
            .select("*")
            .order("scheduled_date")
            .execute()
        )
        events = tuple(self._row_to_expectation(row) for row in (response.data or []))
        return tuple(sorted(events, key=self._list_upcoming_sort_key))

    @staticmethod
    def _list_upcoming_sort_key(event: EventExpectation) -> tuple[bool, int]:
        # Plain ascending scheduled_date would bury today's/upcoming events
        # under accumulating history once past events are retained (see
        # above). Active/upcoming events (today or later) sort first,
        # soonest first; already-released events follow, most recently
        # released first, so the home screen's "Seurannassa" list leads
        # with what's actually current.
        is_past = event.scheduled_date < date.today()
        ordinal = event.scheduled_date.toordinal()
        return (is_past, -ordinal if is_past else ordinal)

    def save(
        self,
        expectation: EventExpectation,
        *,
        change_note: str | None = None,
    ) -> EventExpectation:
        now = datetime.now(UTC)
        self.client.table("market_events").upsert(
            {
                "event_id": expectation.event_id,
                "instrument": expectation.instrument,
                "event_name": expectation.event_name,
                "scheduled_date": expectation.scheduled_date.isoformat(),
                "updated_at": now.isoformat(),
            },
            on_conflict="event_id",
        ).execute()

        payload = {
            "event_id": expectation.event_id,
            "version": self._next_version(expectation.event_id),
            "source_name": expectation.source_name or "manual",
            "source_url": expectation.source_url,
            "source_as_of": expectation.source_as_of.isoformat()
            if expectation.source_as_of
            else None,
            "consensus": expectation.consensus,
            "important_kpis": list(expectation.important_kpis),
            "bull_case": list(expectation.bull_case),
            "base_case": list(expectation.base_case),
            "bear_case": list(expectation.bear_case),
            "triggers": expectation.triggers,
            "invalidation_conditions": list(expectation.invalidation_conditions),
            "change_note": change_note,
        }

        # Concurrent writers can race on version allocation. Retry only the
        # unique-constraint conflict; permission, validation and network errors
        # must surface instead of being hidden by retries.
        for attempt in range(3):
            try:
                response = (
                    self.client.table("event_expectation_versions")
                    .insert(payload)
                    .select("version, created_at")
                    .execute()
                )
                row = (response.data or [{}])[0]
                created_at = self._parse_datetime(row.get("created_at")) or now
                return replace(
                    expectation,
                    version=int(row.get("version", payload["version"])),
                    updated_at=created_at,
                )
            except Exception as exc:
                if not self._is_unique_violation(exc) or attempt == 2:
                    raise
                payload["version"] = self._next_version(expectation.event_id)

        raise RuntimeError("unreachable")

    def _next_version(self, event_id: str) -> int:
        response = (
            self.client.table("event_expectation_versions")
            .select("version")
            .eq("event_id", event_id)
            .order("version", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return int(rows[0]["version"]) + 1 if rows else 1

    @classmethod
    def _row_to_expectation(cls, row: dict[str, Any]) -> EventExpectation:
        return EventExpectation(
            event_id=str(row["event_id"]),
            instrument=str(row["instrument"]),
            event_name=str(row["event_name"]),
            scheduled_date=cls._parse_date(row["scheduled_date"]),
            consensus=dict(row.get("consensus") or {}),
            important_kpis=tuple(row.get("important_kpis") or ()),
            bull_case=tuple(row.get("bull_case") or ()),
            base_case=tuple(row.get("base_case") or ()),
            bear_case=tuple(row.get("bear_case") or ()),
            triggers=dict(row.get("triggers") or {}),
            invalidation_conditions=tuple(row.get("invalidation_conditions") or ()),
            source_name=row.get("source_name"),
            source_url=row.get("source_url"),
            source_as_of=cls._parse_date(row["source_as_of"])
            if row.get("source_as_of")
            else None,
            version=int(row.get("version") or 1),
            updated_at=cls._parse_datetime(row.get("created_at"))
            or datetime.now(UTC),
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

    @staticmethod
    def _is_unique_violation(exc: Exception) -> bool:
        return getattr(exc, "code", None) == "23505" or "23505" in str(exc)
