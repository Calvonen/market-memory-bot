from __future__ import annotations

import os
import uuid
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
from trading_system.calendar_runtime_promotion import SupabaseCalendarRuntimePromotionRepository
from trading_system.calendar_runtime_timing import (
    CalendarRuntimeTiming,
    FinnhubCalendarRuntimeTimingResolver,
)
from trading_system.tracked_event_repository import TrackedEventTimeStatus

# Supabase's Data API (PostgREST) caps a single response at this many rows
# by default (`db-max-rows`). list_upcoming() must never assume one
# response holds the whole result set - a date window with more rows than
# this would otherwise be silently truncated, and later events would
# disappear from the mobile calendar even though they exist in the table.
_LIST_UPCOMING_PAGE_SIZE = 1000


class SupabaseCalendarEventRepository:
    """CalendarEventRepository backed by Supabase Data API.

    Production code uses a backend-only secret/service-role key, same as
    SupabaseEventExpectationRepository. Expo must never receive that key.

    `track()` is the production calendar -> persistent-runtime orchestration
    boundary. It resolves a safe timing only when no runtime is already bound,
    then delegates the actual calendar/runtime mutation to the atomic promotion
    RPC. An existing canonical binding reuses its persisted timing so a retry
    never depends on Finnhub still returning the same `hour` classification.
    """

    def __init__(
        self,
        client: Any,
        *,
        runtime_timing_resolver: Any | None = None,
        runtime_promotion_repository: Any | None = None,
    ) -> None:
        self.client = client
        self._runtime_timing_resolver = runtime_timing_resolver
        self._runtime_promotion_repository = runtime_promotion_repository

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
        # Pages through the complete result set via keyset (cursor)
        # pagination, not PostgREST's .range() (offset-based). Offset
        # pagination re-derives each page from a numeric position that a
        # concurrent insert/update elsewhere in the ordered set silently
        # shifts: a row landing ahead of the current offset boundary pushes
        # every later row one position further out, so the next
        # `.range(offset, ...)` request - still anchored to the old offset -
        # either re-returns a row already seen (duplicate) or, more
        # insidiously, skips exactly the row that used to sit at that
        # boundary (never returned by either page).
        #
        # The cursor is `id` alone, never `scheduled_date` - `id` is the
        # table's immutable primary key (never changes once a row exists),
        # while `scheduled_date` is explicitly *not* immutable: a
        # still-candidate row's date can move on a later sync (see
        # "Idempotent sync" in docs/calendar_watchlist.md). A cursor built
        # from a mutable column can drift out from under an in-flight walk:
        # if a not-yet-fetched row's date moves to sort *behind* the
        # cursor, a (scheduled_date, id)-keyed cursor would silently skip
        # it forever; if an already-fetched row's date moves to sort
        # *ahead* of the cursor, it could be re-fetched as a duplicate.
        # Paginating on `id` alone is immune to both: which page a row
        # lands on depends only on a value that can never change after the
        # row is created, so a concurrent scheduled_date update - on any
        # row, already-fetched or not - can never move it across a page
        # boundary mid-walk. A page shorter than the page size is what
        # ends the loop - the standard, unambiguous "that was the last
        # page" signal, and still deterministic: the loop always
        # terminates in exactly ceil(row_count / page_size) requests,
        # regardless of concurrent writes elsewhere in the table.
        events: list[CalendarEvent] = []
        cursor_id: str | None = None
        while True:
            query = (
                self.client.table("calendar_events")
                .select("*")
                .in_(
                    "status",
                    [CalendarEventStatus.CANDIDATE.value, CalendarEventStatus.TRACKED.value],
                )
                .gte("scheduled_date", from_date.isoformat())
                .lte("scheduled_date", to_date.isoformat())
            )
            if cursor_id is not None:
                query = query.gt("id", cursor_id)
            response = query.order("id").limit(_LIST_UPCOMING_PAGE_SIZE).execute()
            rows = response.data or []
            events.extend(self._row_to_event(row) for row in rows)
            if len(rows) < _LIST_UPCOMING_PAGE_SIZE:
                break
            cursor_id = rows[-1]["id"]
        # The pagination walk itself is ordered by `id` alone (see above) -
        # the caller-facing order (upcoming-soonest-first, matching the
        # mobile sort in mergeUpcomingRows()) is applied once, here, on the
        # complete accumulated result set, entirely independent of however
        # the rows were paginated in. `id` as the tie-break keeps this
        # deterministic for rows sharing the same scheduled_date.
        events.sort(key=lambda event: (event.scheduled_date, event.calendar_event_id))
        return tuple(events)

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
        # Keep API/in-memory lookup representation concerns outside this
        # production storage boundary. PostgreSQL always receives canonical
        # UUID text, including when the API was given a valid dashless UUID.
        canonical_calendar_event_id = str(uuid.UUID(calendar_event_id))
        try:
            response = self.client.rpc(
                "transition_calendar_event_status",
                {
                    "input_calendar_event_id": canonical_calendar_event_id,
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

    def _get_bound_runtime_timing(self, event: CalendarEvent) -> CalendarRuntimeTiming | None:
        response = (
            self.client.table("tracked_market_events")
            .select("instrument,kind,source,external_key,event_at,event_time_status")
            .eq("calendar_event_id", event.calendar_event_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        row = rows[0]
        expected_external_key = f"calendar:{event.calendar_event_id}"
        if (
            str(row.get("instrument") or "") != event.instrument.upper().replace(" ", "")
            or str(row.get("kind") or "") != event.event_type
            or str(row.get("source") or "") != event.source
            or str(row.get("external_key") or "") != expected_external_key
        ):
            raise InvalidCalendarEventTransition(
                "calendar runtime binding identity conflict"
            )
        event_at = self._parse_datetime(row.get("event_at"))
        if event_at is None:
            raise RuntimeError("bound tracked runtime event is missing event_at")
        try:
            event_time_status = TrackedEventTimeStatus(str(row.get("event_time_status") or ""))
        except ValueError as exc:
            raise RuntimeError("bound tracked runtime event has invalid event_time_status") from exc
        return CalendarRuntimeTiming(
            event_at=event_at,
            event_time_status=event_time_status,
            provider_timing="persisted",
        )

    def track(self, calendar_event_id: str) -> CalendarEvent:
        canonical_calendar_event_id = str(uuid.UUID(calendar_event_id))
        event = self.get(canonical_calendar_event_id)
        if event is None:
            raise CalendarEventNotFound(calendar_event_id)
        if event.status not in (CalendarEventStatus.CANDIDATE, CalendarEventStatus.TRACKED):
            raise InvalidCalendarEventTransition(
                f"cannot track a calendar event with status {event.status.value!r}"
            )

        timing = self._get_bound_runtime_timing(event)
        if timing is None:
            resolver = self._runtime_timing_resolver
            if resolver is None:
                resolver = FinnhubCalendarRuntimeTimingResolver.from_env()
                self._runtime_timing_resolver = resolver
            timing = resolver.resolve(event)

        promotion_repository = self._runtime_promotion_repository
        if promotion_repository is None:
            promotion_repository = SupabaseCalendarRuntimePromotionRepository(self.client)
            self._runtime_promotion_repository = promotion_repository
        promotion_repository.promote(event, timing, actor="calendar-track-api")

        refreshed = self.get(canonical_calendar_event_id)
        if refreshed is None:
            raise RuntimeError("calendar event disappeared after runtime promotion")
        if refreshed.status != CalendarEventStatus.TRACKED:
            raise RuntimeError("calendar runtime promotion did not leave event tracked")
        return refreshed

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
