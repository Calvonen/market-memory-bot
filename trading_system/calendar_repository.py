from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
from typing import Iterable, Protocol

from trading_system.calendar_provider import CalendarCandidate
from trading_system.models import new_id, utc_now
from datetime import datetime


class CalendarEventStatus(str, Enum):
    """The full candidate -> ... -> approve lifecycle.

    Only CANDIDATE and TRACKED are reachable through this MVP's API - the
    rest exist so the storage shape does not need to change again once
    research/strategy-preparation/enrichment/preview/approve stages are
    wired up in a later phase. `candidate`/`tracked` themselves must never
    influence the trading worker or the PAPER pipeline; only an eventual
    APPROVED calendar event graduates into an actual EventExpectation/
    market_event, which is out of scope here.
    """

    CANDIDATE = "candidate"
    TRACKED = "tracked"
    RESEARCH = "research"
    STRATEGY_DECISION = "decision_to_prepare_strategy"
    ENRICHED = "enrich_event_details"
    PREVIEW = "preview"
    APPROVED = "approve"


# Statuses this MVP's sync/list/track/untrack operations ever produce or
# accept. A future phase can extend CalendarEventStatus without touching this
# set until the corresponding transitions actually exist.
_LOCKED_FROM_SYNC_OVERWRITE = frozenset(CalendarEventStatus) - {CalendarEventStatus.CANDIDATE}


class CalendarEventNotFound(Exception):
    """No calendar/watchlist event exists for this id."""


class InvalidCalendarEventTransition(Exception):
    """The requested status transition is not valid from the event's current status."""


@dataclass(frozen=True)
class CalendarEvent:
    calendar_event_id: str
    company_name: str
    instrument: str
    market: str
    event_type: str
    scheduled_date: date
    source: str
    occurrence_key: str
    status: CalendarEventStatus = CalendarEventStatus.CANDIDATE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class CalendarSyncResult:
    """Summary of one sync_candidates() call, for worker logging/tests."""

    inserted: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    skipped_locked: tuple[str, ...] = ()


class CalendarEventRepository(Protocol):
    """Storage boundary for candidate/tracked calendar-watchlist events.

    Deliberately separate from EventExpectationRepository: that repository
    is versioned storage for editable pre-event expectations of events
    already promoted into the trading system. A candidate or tracked
    calendar event has no expectation version, no consensus, and must never
    be readable through EventExpectationRepository.get()/list_upcoming() -
    the trading worker and PAPER pipeline only ever see what that repository
    exposes, so keeping this as its own boundary is what keeps
    candidate/tracked events from being able to influence trading at all.
    """

    def get(self, calendar_event_id: str) -> CalendarEvent | None: ...

    def list_upcoming(self, from_date: date, to_date: date) -> tuple[CalendarEvent, ...]: ...

    def sync_candidates(
        self, candidates: Iterable[CalendarCandidate], *, source: str
    ) -> CalendarSyncResult: ...

    def add_manual_event(
        self,
        candidate: CalendarCandidate,
        *,
        status: CalendarEventStatus = CalendarEventStatus.CANDIDATE,
    ) -> CalendarEvent: ...

    def track(self, calendar_event_id: str) -> CalendarEvent: ...

    def untrack(self, calendar_event_id: str) -> CalendarEvent: ...


def _identity_key(instrument: str, event_type: str, source: str, occurrence_key: str) -> tuple[str, str, str, str]:
    # Deliberately excludes scheduled_date: a provider is free to move a
    # still-candidate event's date on a later sync (see sync_candidates()),
    # so the date can never be part of what identifies "the same event".
    # occurrence_key is what distinguishes one recurrence from the next
    # (e.g. "2026Q3" vs "2026Q4") - without it, every quarterly release of
    # the same instrument+event_type+source would collide into one row.
    return (instrument, event_type, source, occurrence_key)


@dataclass
class InMemoryCalendarEventRepository:
    events: dict[str, CalendarEvent] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def get(self, calendar_event_id: str) -> CalendarEvent | None:
        return self.events.get(calendar_event_id)

    def list_upcoming(self, from_date: date, to_date: date) -> tuple[CalendarEvent, ...]:
        matches = [
            event
            for event in self.events.values()
            if event.status in (CalendarEventStatus.CANDIDATE, CalendarEventStatus.TRACKED)
            and from_date <= event.scheduled_date <= to_date
        ]
        return tuple(sorted(matches, key=lambda event: (event.scheduled_date, event.instrument)))

    def _find_by_identity(
        self, instrument: str, event_type: str, source: str, occurrence_key: str
    ) -> CalendarEvent | None:
        key = _identity_key(instrument, event_type, source, occurrence_key)
        for event in self.events.values():
            if (
                _identity_key(event.instrument, event.event_type, event.source, event.occurrence_key)
                == key
            ):
                return event
        return None

    def sync_candidates(
        self, candidates: Iterable[CalendarCandidate], *, source: str
    ) -> CalendarSyncResult:
        inserted: list[str] = []
        updated: list[str] = []
        skipped_locked: list[str] = []

        with self.lock:
            for candidate in candidates:
                existing = self._find_by_identity(
                    candidate.instrument, candidate.event_type, source, candidate.occurrence_key
                )
                if existing is None:
                    event = CalendarEvent(
                        calendar_event_id=new_id(),
                        company_name=candidate.company_name,
                        instrument=candidate.instrument,
                        market=candidate.market,
                        event_type=candidate.event_type,
                        scheduled_date=candidate.scheduled_date,
                        source=source,
                        occurrence_key=candidate.occurrence_key,
                        status=CalendarEventStatus.CANDIDATE,
                    )
                    self.events[event.calendar_event_id] = event
                    inserted.append(event.calendar_event_id)
                    continue

                if existing.status in _LOCKED_FROM_SYNC_OVERWRITE:
                    # Already tracked (or beyond): a provider re-sync must
                    # never silently move the date or identity fields out
                    # from under a user's tracked selection, and must never
                    # drop the status back to candidate.
                    skipped_locked.append(existing.calendar_event_id)
                    continue

                merged = replace(
                    existing,
                    company_name=candidate.company_name,
                    market=candidate.market,
                    scheduled_date=candidate.scheduled_date,
                    updated_at=utc_now(),
                )
                self.events[existing.calendar_event_id] = merged
                updated.append(existing.calendar_event_id)

        return CalendarSyncResult(
            inserted=tuple(inserted), updated=tuple(updated), skipped_locked=tuple(skipped_locked)
        )

    def add_manual_event(
        self,
        candidate: CalendarCandidate,
        *,
        status: CalendarEventStatus = CalendarEventStatus.CANDIDATE,
    ) -> CalendarEvent:
        with self.lock:
            existing = self._find_by_identity(
                candidate.instrument, candidate.event_type, candidate.source, candidate.occurrence_key
            )
            if existing is not None:
                # Matches SupabaseCalendarEventRepository.add_manual_event():
                # a request for the same identity that now asks for
                # status=TRACKED must still apply that transition to an
                # existing candidate, not just return it unchanged -
                # otherwise this in-memory repository (used for local runs
                # and tests) would silently diverge from production
                # behavior for this exact request.
                if status == CalendarEventStatus.TRACKED and existing.status == CalendarEventStatus.CANDIDATE:
                    updated = replace(existing, status=CalendarEventStatus.TRACKED, updated_at=utc_now())
                    self.events[existing.calendar_event_id] = updated
                    return updated
                return existing
            event = CalendarEvent(
                calendar_event_id=new_id(),
                company_name=candidate.company_name,
                instrument=candidate.instrument,
                market=candidate.market,
                event_type=candidate.event_type,
                scheduled_date=candidate.scheduled_date,
                source=candidate.source,
                occurrence_key=candidate.occurrence_key,
                status=status,
            )
            self.events[event.calendar_event_id] = event
            return event

    def track(self, calendar_event_id: str) -> CalendarEvent:
        with self.lock:
            existing = self.events.get(calendar_event_id)
            if existing is None:
                raise CalendarEventNotFound(calendar_event_id)
            if existing.status == CalendarEventStatus.TRACKED:
                return existing
            if existing.status != CalendarEventStatus.CANDIDATE:
                raise InvalidCalendarEventTransition(
                    f"cannot track a calendar event with status {existing.status.value!r}"
                )
            updated = replace(existing, status=CalendarEventStatus.TRACKED, updated_at=utc_now())
            self.events[calendar_event_id] = updated
            return updated

    def untrack(self, calendar_event_id: str) -> CalendarEvent:
        with self.lock:
            existing = self.events.get(calendar_event_id)
            if existing is None:
                raise CalendarEventNotFound(calendar_event_id)
            if existing.status == CalendarEventStatus.CANDIDATE:
                return existing
            if existing.status != CalendarEventStatus.TRACKED:
                raise InvalidCalendarEventTransition(
                    f"cannot untrack a calendar event with status {existing.status.value!r}"
                )
            updated = replace(existing, status=CalendarEventStatus.CANDIDATE, updated_at=utc_now())
            self.events[calendar_event_id] = updated
            return updated
