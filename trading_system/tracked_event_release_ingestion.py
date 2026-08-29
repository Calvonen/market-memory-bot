from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from trading_system.ai_event_analyzer import EventAnalyzer
from trading_system.manual_release_ingestion import ManualOfficialReleaseProvider
from trading_system.models import EventExpectation
from trading_system.official_release_source_repository import OfficialReleaseSourceState
from trading_system.release_worker import EventReleaseMonitor
from trading_system.results_page_release_ingestion import ResultsPageOfficialReleaseProvider
from trading_system.tracked_event_repository import PersistentTrackedEvent
from trading_system.workflow_readiness_evidence_loader import canonical_release_event_id


class ReleaseIngestionNotReady(RuntimeError):
    """The canonical release workflow is incomplete or has conflicting identity."""


class ExpectationRepository(Protocol):
    def get(self, event_id: str) -> EventExpectation | None: ...


class OfficialSourceRepository(Protocol):
    def get_state(self, event_id: str) -> OfficialReleaseSourceState: ...


class ReleaseRepository(Protocol):
    def has_analysis_for_event_version(
        self, *, event_id: str, expectation_version: int
    ) -> bool: ...


class ReleaseShellRepository(Protocol):
    def ensure_release_shell(self, event: PersistentTrackedEvent) -> str: ...


class IngestionAuditRepository(Protocol):
    def record_attempt(
        self, *, tracked_event_id: str, release_event_id: str, actor: str, status: str
    ) -> None: ...


class SupabaseTrackedEventReleaseShellRepository:
    """Invoke the canonical database identity/binding validator."""

    def __init__(self, client) -> None:
        self.client = client

    def ensure_release_shell(self, event: PersistentTrackedEvent) -> str:
        response = self.client.rpc(
            "ensure_tracked_event_release_shell_with_blocker",
            {"input_tracked_event_id": event.event_id},
        ).execute()
        rows = list(response.data or [])
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("Canonical release-shell RPC returned invalid response")
        blocker = str(rows[0].get("out_blocker_code") or "").strip()
        if blocker:
            raise ReleaseIngestionNotReady(blocker)
        release_event_id = str(rows[0].get("out_release_event_id") or "").strip()
        if not release_event_id:
            raise RuntimeError("Canonical release-shell RPC omitted release identity")
        return release_event_id


class SupabaseTrackedEventReleaseIngestionAuditRepository:
    """Durably attribute explicit operator ingestion attempts."""

    def __init__(self, client) -> None:
        self.client = client

    def record_attempt(
        self, *, tracked_event_id: str, release_event_id: str, actor: str, status: str
    ) -> None:
        self.client.rpc(
            "record_tracked_event_release_ingestion_attempt",
            {
                "input_tracked_event_id": tracked_event_id,
                "input_release_event_id": release_event_id,
                "input_actor": actor,
                "input_status": status,
            },
        ).execute()


@dataclass(frozen=True)
class TrackedEventReleaseIngestionResult:
    event_id: str
    release_event_id: str
    status: str
    source_document_id: str | None = None
    analysis_id: str | None = None
    message: str | None = None
    overdue: bool = False


class _PinnedExpectationRepository:
    def __init__(self, expectation: EventExpectation) -> None:
        self.expectation = expectation

    def get(self, event_id: str) -> EventExpectation | None:
        return self.expectation if event_id == self.expectation.event_id else None


def ingest_tracked_event_release_once(
    event: PersistentTrackedEvent,
    *,
    expectation_repository: ExpectationRepository,
    official_release_source_repository: OfficialSourceRepository,
    release_repository: ReleaseRepository,
    release_shell_repository: ReleaseShellRepository,
    ingestion_audit_repository: IngestionAuditRepository,
    analyzer_factory: Callable[[], EventAnalyzer],
    actor: str,
) -> TrackedEventReleaseIngestionResult:
    """Run one explicit attempt through the existing canonical ingestion engine."""
    release_event_id = canonical_release_event_id(event)
    validated_release_event_id = release_shell_repository.ensure_release_shell(event)
    if validated_release_event_id != release_event_id:
        raise ReleaseIngestionNotReady(
            "Canonical release-shell identity does not match tracked event"
        )
    expectation = expectation_repository.get(release_event_id)
    if expectation is None:
        raise ReleaseIngestionNotReady("Canonical release expectation is missing")
    if expectation.event_id != release_event_id:
        raise ReleaseIngestionNotReady("Canonical release expectation identity does not match")
    if expectation.instrument.strip().upper().replace(" ", "") != event.instrument.strip().upper().replace(" ", ""):
        raise ReleaseIngestionNotReady("Canonical release expectation instrument does not match tracked event")

    source_state = official_release_source_repository.get_state(release_event_id)
    source = source_state.source
    if source is None:
        raise ReleaseIngestionNotReady("No active approved official release source")
    if source.event_id != release_event_id:
        raise ReleaseIngestionNotReady("Approved official release source identity does not match")

    if release_repository.has_analysis_for_event_version(
        event_id=release_event_id, expectation_version=expectation.version
    ):
        ingestion_audit_repository.record_attempt(
            tracked_event_id=event.event_id,
            release_event_id=release_event_id,
            actor=actor,
            status="already_analyzed",
        )
        return TrackedEventReleaseIngestionResult(
            event_id=event.event_id,
            release_event_id=release_event_id,
            status="already_analyzed",
            message="Current expectation version is already analyzed",
        )

    if source.source_kind == "direct_url":
        provider = ManualOfficialReleaseProvider(source)
    elif source.source_kind == "results_page":
        provider = ResultsPageOfficialReleaseProvider.for_event(
            source, scheduled_date=expectation.scheduled_date
        )
    else:
        raise ReleaseIngestionNotReady("Approved official release source kind is invalid")

    ingestion = EventReleaseMonitor(
        expectation_repository=_PinnedExpectationRepository(expectation),
        release_repository=release_repository,
        analyzer=analyzer_factory(),
        provider=provider,
    ).run_once(release_event_id)
    ingestion_audit_repository.record_attempt(
        tracked_event_id=event.event_id,
        release_event_id=release_event_id,
        actor=actor,
        status=ingestion.status,
    )
    return TrackedEventReleaseIngestionResult(
        event_id=event.event_id,
        release_event_id=release_event_id,
        status=ingestion.status,
        source_document_id=ingestion.source_document_id,
        analysis_id=ingestion.analysis_id,
        message=ingestion.message,
        overdue=ingestion.overdue,
    )
