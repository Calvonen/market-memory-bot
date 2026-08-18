from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol

from trading_system.models import EventExpectation, utc_now


class EventExpectationRepository(Protocol):
    """Storage boundary for editable pre-event expectations.

    Production can use Supabase; tests and offline tooling can use the in-memory
    implementation without changing event or strategy logic.
    """

    def get(self, event_id: str) -> EventExpectation | None: ...

    def save(
        self,
        expectation: EventExpectation,
        *,
        change_note: str | None = None,
    ) -> EventExpectation: ...

    def list_upcoming(self) -> tuple[EventExpectation, ...]: ...


@dataclass
class InMemoryEventExpectationRepository:
    events: dict[str, EventExpectation] = field(default_factory=dict)

    def get(self, event_id: str) -> EventExpectation | None:
        return self.events.get(event_id)

    def save(
        self,
        expectation: EventExpectation,
        *,
        change_note: str | None = None,
    ) -> EventExpectation:
        previous = self.events.get(expectation.event_id)
        version = previous.version + 1 if previous else max(1, expectation.version)
        saved = replace(expectation, version=version, updated_at=utc_now())
        self.events[expectation.event_id] = saved
        return saved

    def list_upcoming(self) -> tuple[EventExpectation, ...]:
        return tuple(sorted(self.events.values(), key=lambda item: item.scheduled_date))
