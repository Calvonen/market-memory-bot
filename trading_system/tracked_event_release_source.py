from __future__ import annotations

from dataclasses import dataclass

from trading_system.official_release_source_repository import OfficialReleaseSourceState
from trading_system.tracked_event_repository import PersistentTrackedEvent
from trading_system.workflow_readiness_evidence_loader import canonical_release_event_id


@dataclass(frozen=True)
class TrackedEventReleaseSourceReadModel:
    event_id: str
    release_event_id: str
    active: bool
    version: int
    source_kind: str | None
    source_url: str | None
    source_title: str | None


def build_tracked_event_release_source_read_model(
    event: PersistentTrackedEvent,
    state: OfficialReleaseSourceState,
) -> TrackedEventReleaseSourceReadModel:
    release_event_id = canonical_release_event_id(event)
    source = state.source

    if state.version < 0:
        raise ValueError("official release source version must not be negative")
    if source is None:
        return TrackedEventReleaseSourceReadModel(
            event_id=event.event_id,
            release_event_id=release_event_id,
            active=False,
            version=state.version,
            source_kind=None,
            source_url=None,
            source_title=None,
        )

    if source.event_id != release_event_id:
        raise ValueError("official release source identity does not match tracked event")
    if source.version != state.version:
        raise ValueError("official release source version does not match canonical state")

    return TrackedEventReleaseSourceReadModel(
        event_id=event.event_id,
        release_event_id=release_event_id,
        active=True,
        version=state.version,
        source_kind=source.source_kind,
        source_url=source.source_url,
        source_title=source.source_title,
    )
