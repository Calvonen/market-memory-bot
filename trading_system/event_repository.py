from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from trading_system.models import EventExpectation


class EventExpectationRepository(Protocol):
    """Storage boundary for editable pre-event expectations.

    Production can use Supabase; tests and offline tooling can use the in-memory
    implementation without changing event or strategy logic.
    """

    def get(self, event_id: str) -> EventExpectation | None: ...

    def save(self, expectation: EventExpectation) -> EventExpectation: ...

    def list_upcoming(self) -> tuple[EventExpectation, ...]: ...


@dataclass
class InMemoryEventExpectationRepository:
    events: dict[str, EventExpectation] = field(default_factory=dict)

    def get(self, event_id: str) -> EventExpectation | None:
        return self.events.get(event_id)

    def save(self, expectation: EventExpectation) -> EventExpectation:
        self.events[expectation.event_id] = expectation
        return expectation

    def list_upcoming(self) -> tuple[EventExpectation, ...]:
        return tuple(sorted(self.events.values(), key=lambda item: item.scheduled_date))
