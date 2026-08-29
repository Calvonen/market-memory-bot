from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_system.tracked_event_repository import PersistentTrackedEvent
from trading_system.workflow_readiness_evidence_loader import canonical_release_event_id

MAX_RELEASE_SKIP_REASON_LENGTH = 1000


class ReleaseSkipConflict(RuntimeError):
    """The existing canonical release binding is missing or conflicts with the tracked event."""


class ReleaseSkipNotFound(RuntimeError):
    """The tracked event disappeared before the atomic skip audit could lock it."""


class ReleaseSkipAuditRepository(Protocol):
    def record_skip(
        self, *, tracked_event_id: str, release_event_id: str, actor: str, reason: str
    ) -> None: ...


class SupabaseTrackedEventReleaseSkipAuditRepository:
    """Atomically validate the existing binding and persist the skip audit."""

    _CONFLICT_MARKERS = (
        "tracked_release_",
        "tracked_event_not_release_shell_eligible",
        "tracked_event_release_date_required",
    )

    def __init__(self, client) -> None:
        self.client = client

    def record_skip(
        self, *, tracked_event_id: str, release_event_id: str, actor: str, reason: str
    ) -> None:
        try:
            self.client.rpc(
                "record_tracked_event_release_skip",
                {
                    "input_tracked_event_id": tracked_event_id,
                    "input_release_event_id": release_event_id,
                    "input_actor": actor,
                    "input_reason": reason,
                },
            ).execute()
        except Exception as exc:
            message = str(exc)
            if "tracked_event_not_found" in message or "P0002" in message:
                raise ReleaseSkipNotFound(message) from exc
            if any(marker in message for marker in self._CONFLICT_MARKERS):
                raise ReleaseSkipConflict(message) from exc
            raise


@dataclass(frozen=True)
class TrackedEventReleaseSkipResult:
    event_id: str
    release_event_id: str
    status: str = "skipped"


def skip_tracked_event_release(
    event: PersistentTrackedEvent,
    *,
    audit_repository: ReleaseSkipAuditRepository,
    actor: str,
    reason: str,
) -> TrackedEventReleaseSkipResult:
    """Append an audited skip; the RPC validates the existing binding atomically."""
    actor = actor.strip()
    reason = reason.strip()
    if not actor or len(actor) > 200:
        raise ValueError("actor must be nonblank and at most 200 characters")
    if not reason or len(reason) > MAX_RELEASE_SKIP_REASON_LENGTH:
        raise ValueError(
            f"reason must be nonblank and at most {MAX_RELEASE_SKIP_REASON_LENGTH} characters"
        )

    release_event_id = canonical_release_event_id(event)
    audit_repository.record_skip(
        tracked_event_id=event.event_id,
        release_event_id=release_event_id,
        actor=actor,
        reason=reason,
    )
    return TrackedEventReleaseSkipResult(
        event_id=event.event_id, release_event_id=release_event_id
    )
