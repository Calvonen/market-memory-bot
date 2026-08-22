from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
from typing import Iterable, Protocol
from uuid import UUID

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


_LOCKED_FROM_SYNC_OVERWRITE = frozenset(CalendarEventStatus) - {CalendarEventStatus.CANDIDATE}

_UUID_DASHED_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_UUID_DASHLESS_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _canonical_uuid_text(value: str) -> str | None:
    if not (_UUID_DASHED_RE.fullmatch(value) or _UUID_DASHLESS_RE.fullmatch(value)):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


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
    inserted: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    skipped_locked: tuple[str, ...] = ()


class CalendarEventRepository(Protocol):
    def get(self, calendar_event_id: str) -> CalendarEvent | None: ...
    def list_upcoming(self, from_date: date, to_date: date) -> tuple[CalendarEvent, ...]: ...
    def sync_candidates(self, candidates: Iterable[CalendarCandidate], *, source: str) -> CalendarSyncResult: ...
    def add_manual_event(
        self,
        candidate: CalendarCandidate,
        *,
        status: CalendarEventStatus = CalendarEventStatus.CANDIDATE,
    ) -> CalendarEvent: ...
    def track(self, calendar_event_id: str) -> CalendarEvent: ...
    def untrack(self, calendar_event_id: str) -> CalendarEvent: ...


def _identity_key(instrument: str, event_type: str, source: str, occurrence_key: str) -> tuple[str, str, str, str]:
    return (instrument, event_type, source, occurrence_key)


def _merged_candidate_metadata(existing: CalendarEvent, candidate: CalendarCandidate) -> tuple[str, str]:
    """Merge provider metadata without letting a fallback erase enrichment.

    Finnhub's earnings-calendar response often contains only a symbol/date.
    In that case the provider deliberately emits company_name=instrument and
    market="Unknown". Those are placeholders, not authoritative corrections.
    Once a candidate has a better value, a later transient enrichment failure
    must preserve it. Real non-placeholder values still replace older values.
    """
    incoming_name_is_placeholder = candidate.company_name == candidate.instrument
    existing_name_is_enriched = existing.company_name != existing.instrument
    company_name = (
        existing.company_name
        if incoming_name_is_placeholder and existing_name_is_enriched
        else candidate.company_name
    )

    market = (
        existing.market
        if candidate.market == "Unknown" and existing.market != "Unknown"
        else candidate.market
    )
    return company_name, market


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
            if _identity_key(event.instrument, event.event_type, event.source, event.occurrence_key) == key:
                return event
        return None

    def _resolve_event_key(self, calendar_event_id: str) -> str | None:
        if calendar_event_id in self.events:
            return calendar_event_id
        canonical = _canonical_uuid_text(calendar_event_id)
        if canonical is None:
            return None
        for stored_key in self.events:
            if _canonical_uuid_text(stored_key) == canonical:
                return stored_key
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
                    skipped_locked.append(existing.calendar_event_id)
                    continue

                company_name, market = _merged_candidate_metadata(existing, candidate)
                merged = replace(
                    existing,
                    company_name=company_name,
                    market=market,
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
                current = existing
                if current.status == CalendarEventStatus.CANDIDATE:
                    company_name, market = _merged_candidate_metadata(current, candidate)
                    current = replace(
                        current,
                        company_name=company_name,
                        market=market,
                        scheduled_date=candidate.scheduled_date,
                        updated_at=utc_now(),
                    )
                if status == CalendarEventStatus.TRACKED and current.status == CalendarEventStatus.CANDIDATE:
                    current = replace(current, status=CalendarEventStatus.TRACKED, updated_at=utc_now())
                self.events[existing.calendar_event_id] = current
                return current
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
            stored_key = self._resolve_event_key(calendar_event_id)
            if stored_key is None:
                raise CalendarEventNotFound(calendar_event_id)
            existing = self.events[stored_key]
            if existing.status == CalendarEventStatus.TRACKED:
                return existing
            if existing.status != CalendarEventStatus.CANDIDATE:
                raise InvalidCalendarEventTransition(
                    f"cannot track a calendar event with status {existing.status.value!r}"
                )
            updated = replace(existing, status=CalendarEventStatus.TRACKED, updated_at=utc_now())
            self.events[stored_key] = updated
            return updated

    def untrack(self, calendar_event_id: str) -> CalendarEvent:
        with self.lock:
            stored_key = self._resolve_event_key(calendar_event_id)
            if stored_key is None:
                raise CalendarEventNotFound(calendar_event_id)
            existing = self.events[stored_key]
            if existing.status == CalendarEventStatus.CANDIDATE:
                return existing
            if existing.status != CalendarEventStatus.TRACKED:
                raise InvalidCalendarEventTransition(
                    f"cannot untrack a calendar event with status {existing.status.value!r}"
                )
            updated = replace(existing, status=CalendarEventStatus.CANDIDATE, updated_at=utc_now())
            self.events[stored_key] = updated
            return updated
