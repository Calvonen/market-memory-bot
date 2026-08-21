from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Protocol

from trading_system.models import EventExpectation, utc_now


class EventExpectationNotFound(Exception):
    """No expectation version exists yet for this event_id."""


@dataclass(frozen=True)
class ExpectationPatch:
    """A partial patch of event_expectation_versions' editable fields for
    the admin write endpoint. `None` on any field means "leave this field
    unchanged" - never "clear it" - mirroring ExpectationVersionRequest
    (trading_system/api.py) at the repository boundary, deliberately
    without that Pydantic model as a dependency here.

    Resolving a None field against "whatever is currently persisted" must
    happen while a per-event lock is held, and against a read taken *after*
    that lock is acquired - see apply_partial_update() below. Passing this
    patch straight through (rather than a fully pre-merged EventExpectation
    built from a read taken before any lock) is what makes that possible:
    there is deliberately no "current" value baked into a patch for the
    resolver to unknowingly work from.
    """

    consensus: dict[str, float | str | None] | None = None
    important_kpis: tuple[str, ...] | None = None
    bull_case: tuple[str, ...] | None = None
    base_case: tuple[str, ...] | None = None
    bear_case: tuple[str, ...] | None = None
    triggers: dict[str, float | str] | None = None
    invalidation_conditions: tuple[str, ...] | None = None
    source_name: str | None = None
    source_url: str | None = None
    source_as_of: date | None = None


class EventExpectationRepository(Protocol):
    """Storage boundary for editable pre-event expectations.

    Production can use Supabase; tests and offline tooling can use the in-memory
    implementation without changing event or strategy logic.
    """

    def get(self, event_id: str) -> EventExpectation | None: ...

    def apply_partial_update(
        self,
        event_id: str,
        patch: ExpectationPatch,
        *,
        change_note: str,
    ) -> EventExpectation: ...

    def list_upcoming(self) -> tuple[EventExpectation, ...]: ...


@dataclass
class InMemoryEventExpectationRepository:
    events: dict[str, EventExpectation] = field(default_factory=dict)
    # Shared with any StrategyDraftApprovalRepository constructed against
    # this same instance (see strategy_draft_repository.py), so the two
    # writers of `events` in tests contend on the *same* lock - mirroring
    # the single pg_advisory_xact_lock both writers take in production
    # (see supabase/migrations/*_shared_expectation_version_lock.sql).
    # Without this, a concurrent admin write and strategy-draft approval
    # could interleave their read-then-write steps here exactly like they
    # would against an unlocked table.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def get(self, event_id: str) -> EventExpectation | None:
        return self.events.get(event_id)

    def apply_partial_update(
        self,
        event_id: str,
        patch: ExpectationPatch,
        *,
        change_note: str,
    ) -> EventExpectation:
        # `current` is read *inside* the lock, and the merged result is
        # written before the lock is released - so a concurrent
        # InMemoryStrategyDraftApprovalRepository.approve() for the same
        # event_id (sharing this same lock) can never commit a version
        # in between this read and this write. The previous approach read
        # "current" via a separate, unlocked get() call before this method
        # ever ran, then passed a fully pre-merged EventExpectation in here
        # - so an approval that committed in that window would have its
        # untouched fields silently reverted by this method's write.
        with self.lock:
            current = self.events.get(event_id)
            if current is None:
                raise EventExpectationNotFound(event_id)
            merged = replace(
                current,
                consensus=patch.consensus if patch.consensus is not None else current.consensus,
                important_kpis=patch.important_kpis if patch.important_kpis is not None else current.important_kpis,
                bull_case=patch.bull_case if patch.bull_case is not None else current.bull_case,
                base_case=patch.base_case if patch.base_case is not None else current.base_case,
                bear_case=patch.bear_case if patch.bear_case is not None else current.bear_case,
                triggers=patch.triggers if patch.triggers is not None else current.triggers,
                invalidation_conditions=patch.invalidation_conditions
                if patch.invalidation_conditions is not None
                else current.invalidation_conditions,
                source_name=patch.source_name if patch.source_name is not None else current.source_name,
                source_url=patch.source_url if patch.source_url is not None else current.source_url,
                source_as_of=patch.source_as_of if patch.source_as_of is not None else current.source_as_of,
                version=current.version + 1,
                updated_at=utc_now(),
            )
            self.events[event_id] = merged
            return merged

    def list_upcoming(self) -> tuple[EventExpectation, ...]:
        return tuple(sorted(self.events.values(), key=lambda item: item.scheduled_date))
