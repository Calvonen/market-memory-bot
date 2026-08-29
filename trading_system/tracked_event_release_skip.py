from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_system.tracked_event_repository import PersistentTrackedEvent
from trading_system.tracked_event_release_ingestion import (
    ReleaseIngestionNotReady,
    ReleaseShellRepository,
)
from trading_system.workflow_readiness_evidence_loader import canonical_release_event_id

MAX_RELEASE_SKIP_REASON_LENGTH = 1000


class ReleaseSkipAuditRepository(Protocol):
    def record_skip(
        self, *, tracked_event_id: str, release_event_id: str, actor: str, reason: str
    ) -> None: ...


class SupabaseTrackedEventReleaseSkipAuditRepository:
    """Persist release skips only through the restricted canonical audit RPC."""

    def __init__(self, client) -> None:
        self.client = client

    def record_skip(
        self, *, tracked_event_id: str, release_event_id: str, actor: str, reason: str
    ) -> None:
        self.client.rpc(
            "record_tracked_event_release_skip",
            {
                "input_tracked_event_id": tracked_event_id,
                "input_release_event_id": release_event_id,
                "input_actor": actor,
                "input_reason": reason,
            },
        ).execute()


@dataclass(frozen=True)
class TrackedEventReleaseSkipResult:
    event_id: str
    release_event_id: str
    status: str = "skipped"


def skip_tracked_event_release(
    event: PersistentTrackedEvent,
    *,
    release_shell_repository: ReleaseShellRepository,
    audit_repository: ReleaseSkipAuditRepository,
    actor: str,
    reason: str,
) -> TrackedEventReleaseSkipResult:
    """Validate canonical identity and append an audit; do not mutate workflow state."""
    actor = actor.strip()
    reason = reason.strip()
    if not actor or len(actor) > 200:
        raise ValueError("actor must be nonblank and at most 200 characters")
    if not reason or len(reason) > MAX_RELEASE_SKIP_REASON_LENGTH:
        raise ValueError(
            f"reason must be nonblank and at most {MAX_RELEASE_SKIP_REASON_LENGTH} characters"
        )

    release_event_id = canonical_release_event_id(event)
    validated_release_event_id = release_shell_repository.ensure_release_shell(event)
    if validated_release_event_id != release_event_id:
        raise ReleaseIngestionNotReady(
            "Canonical release-shell identity does not match tracked event"
        )

    audit_repository.record_skip(
        tracked_event_id=event.event_id,
        release_event_id=release_event_id,
        actor=actor,
        reason=reason,
    )
    return TrackedEventReleaseSkipResult(
        event_id=event.event_id, release_event_id=release_event_id
    )
