from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from trading_system.assistant_event_proposal import (
    AssistantEventProposalRecord,
    ExistingProposalApproval,
    proposal_approval_via,
    validate_proposal_for_materialization,
)
from trading_system.calendar_release_worker import CalendarReleaseTarget
from trading_system.etoro_instrument_resolver import InstrumentResolutionRequest, ResolvedEtoroInstrument
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceState,
    OfficialReleaseSourceVersionConflict,
)
from trading_system.strategy_draft import draft_fingerprint, identity_mismatches, normalize_draft
from trading_system.strategy_draft_repository import ExpectationVersionConflict
from trading_system.tracked_event_repository import TrackedEventTimeStatus
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


class AssistantProposalMaterializationError(RuntimeError):
    pass


class AssistantProposalInstrumentResolutionError(AssistantProposalMaterializationError):
    pass


class AssistantProposalCanonicalIdentityConflict(AssistantProposalMaterializationError):
    pass


class AssistantProposalOfficialSourceConflict(AssistantProposalMaterializationError):
    pass


class ProposalRepository(Protocol):
    def get(self, proposal_id: str) -> AssistantEventProposalRecord | None: ...
    def find_approval(self, *, approved_via: str) -> ExistingProposalApproval | None: ...
    def mark_materialized(self, proposal_id: str) -> None: ...


class InstrumentResolver(Protocol):
    def resolve(self, request: InstrumentResolutionRequest) -> ResolvedEtoroInstrument | None: ...


class TrackedInstrumentRegistry(Protocol):
    def upsert(self, *, instrument: str, company_name: str, market: str, source: str, actor: str) -> Any: ...


class CanonicalEventIngress(Protocol):
    def register_for_tracked_instrument(self, tracked: TrackedEtoroInstrument, **kwargs: Any) -> Any: ...


class ReleaseTargetRepository(Protocol):
    def ensure_release_shell(self, target: CalendarReleaseTarget) -> str: ...


class ExpectationRepository(Protocol):
    def get(self, event_id: str) -> Any | None: ...


class OfficialSourceRepository(Protocol):
    def get_state(self, event_id: str) -> OfficialReleaseSourceState: ...
    def set(self, source: OfficialReleaseSource, *, expected_version: int, actor: str) -> OfficialReleaseSource: ...


class StrategyApprovalRepository(Protocol):
    def approve(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class AssistantProposalMaterializationResult:
    proposal_id: str
    tracked_event_id: str
    event_id: str
    expectation_version: int
    retried: bool


class AssistantProposalMaterializer:
    """Compose approved assistant proposals into existing canonical event workflow.

    This class deliberately has no trading-task, risk-engine or broker dependency.
    Materialization creates tracking/expectation state only; DEMO/PAPER permission
    remains a separate user-controlled workflow.
    """

    def __init__(
        self,
        *,
        proposals: ProposalRepository,
        resolver: InstrumentResolver,
        registry: TrackedInstrumentRegistry,
        events: CanonicalEventIngress,
        release_targets: ReleaseTargetRepository,
        expectations: ExpectationRepository,
        official_sources: OfficialSourceRepository,
        approvals: StrategyApprovalRepository,
        actor: str = "assistant_proposal_materializer",
    ) -> None:
        self.proposals = proposals
        self.resolver = resolver
        self.registry = registry
        self.events = events
        self.release_targets = release_targets
        self.expectations = expectations
        self.official_sources = official_sources
        self.approvals = approvals
        self.actor = actor

    def materialize(self, proposal_id: str) -> AssistantProposalMaterializationResult:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise AssistantProposalMaterializationError("assistant proposal not found")
        payload = validate_proposal_for_materialization(proposal)

        resolved = self.resolver.resolve(
            InstrumentResolutionRequest(
                instrument=payload.instrument,
                company_name=payload.company_name,
                market=payload.market,
            )
        )
        if resolved is None:
            raise AssistantProposalInstrumentResolutionError(
                "assistant proposal instrument could not be resolved unambiguously"
            )
        if _symbol(resolved.symbol) != _symbol(payload.instrument):
            raise AssistantProposalCanonicalIdentityConflict(
                "resolved eToro symbol differs from proposal instrument"
            )

        registry_row = self.registry.upsert(
            instrument=payload.instrument,
            company_name=payload.company_name,
            market=payload.market,
            source="manual",
            actor=self.actor,
        )
        tracked = TrackedEtoroInstrument(
            tracked_instrument_id=str(registry_row.id),
            instrument=str(registry_row.instrument),
            market=str(registry_row.market),
            etoro_instrument_id=resolved.instrument_id,
            etoro_symbol=resolved.symbol,
            etoro_display_name=resolved.display_name,
            etoro_market=resolved.market,
        )
        if _symbol(tracked.instrument) != _symbol(payload.instrument):
            raise AssistantProposalCanonicalIdentityConflict(
                "tracked instrument registry returned a different instrument"
            )

        event_write = self.events.register_for_tracked_instrument(
            tracked,
            company_name=payload.company_name,
            source="manual",
            external_key=proposal.proposal_key,
            kind=payload.kind,
            title=payload.title,
            event_at=payload.event_at,
            event_date=payload.scheduled_date,
            event_time_status=TrackedEventTimeStatus(payload.event_time_status),
            actor=self.actor,
        )
        tracked_event_id = str(event_write.event_id)
        event_id = f"tracked:{tracked_event_id}"
        target = CalendarReleaseTarget(
            calendar_event_id=None,
            event_id=event_id,
            ticker=tracked.instrument,
            scheduled_date=payload.scheduled_date,
            market=tracked.market,
            tracked_event_id=tracked_event_id,
        )
        release_event_id = self.release_targets.ensure_release_shell(target)
        if release_event_id != event_id:
            raise AssistantProposalCanonicalIdentityConflict(
                "release shell returned a different event identity"
            )

        current = self.expectations.get(event_id)
        if current is None:
            raise AssistantProposalCanonicalIdentityConflict(
                "canonical release shell expectation is missing"
            )
        normalized = normalize_draft(event_id, payload.strategy)
        mismatches = identity_mismatches(normalized, current)
        if mismatches:
            raise AssistantProposalCanonicalIdentityConflict("; ".join(mismatches))

        self._ensure_official_source(event_id=event_id, payload=payload)

        approved_via = proposal_approval_via(proposal)
        fingerprint = draft_fingerprint(normalized)
        receipt = self.proposals.find_approval(approved_via=approved_via)
        if receipt is not None:
            version = self._matching_receipt_version(receipt, event_id, fingerprint)
            self.proposals.mark_materialized(proposal.id)
            return AssistantProposalMaterializationResult(
                proposal_id=proposal.id,
                tracked_event_id=tracked_event_id,
                event_id=event_id,
                expectation_version=version,
                retried=True,
            )

        try:
            approved = self.approvals.approve(
                event_id=event_id,
                expected_base_version=current.version,
                source_name=normalized["source_name"],
                source_url=normalized["source_url"],
                source_as_of=payload.strategy.source_as_of,
                consensus=normalized["consensus"],
                important_kpis=normalized["important_kpis"],
                bull_case=normalized["bull_case"],
                base_case=normalized["base_case"],
                bear_case=normalized["bear_case"],
                triggers=normalized["triggers"],
                invalidation_conditions=normalized["invalidation_conditions"],
                change_note=normalized["change_note"],
                draft_fingerprint=fingerprint,
                approved_by=proposal.reviewed_by or "",
                approved_via=approved_via,
            )
            version = int(approved.version)
        except ExpectationVersionConflict:
            receipt = self.proposals.find_approval(approved_via=approved_via)
            if receipt is None:
                raise
            version = self._matching_receipt_version(receipt, event_id, fingerprint)

        self.proposals.mark_materialized(proposal.id)
        return AssistantProposalMaterializationResult(
            proposal_id=proposal.id,
            tracked_event_id=tracked_event_id,
            event_id=event_id,
            expectation_version=version,
            retried=False,
        )

    def _ensure_official_source(self, *, event_id: str, payload: Any) -> None:
        desired = OfficialReleaseSource(
            event_id=event_id,
            source_kind=payload.official_source.source_kind,
            source_url=payload.official_source.source_url,
            source_title=payload.official_source.source_title,
        )
        state = self.official_sources.get_state(event_id)
        if state.source is not None:
            if _same_source(state.source, desired):
                return
            raise AssistantProposalOfficialSourceConflict(
                "event already has a different approved official release source"
            )
        try:
            self.official_sources.set(
                desired,
                expected_version=state.version,
                actor=self.actor,
            )
        except OfficialReleaseSourceVersionConflict:
            latest = self.official_sources.get_state(event_id)
            if latest.source is not None and _same_source(latest.source, desired):
                return
            raise AssistantProposalOfficialSourceConflict(
                "official release source changed concurrently"
            )

    @staticmethod
    def _matching_receipt_version(
        receipt: ExistingProposalApproval,
        event_id: str,
        fingerprint: str,
    ) -> int:
        if receipt.event_id != event_id or receipt.draft_fingerprint != fingerprint:
            raise AssistantProposalCanonicalIdentityConflict(
                "existing proposal approval receipt does not match this materialization"
            )
        return receipt.expectation_version


def _same_source(left: OfficialReleaseSource, right: OfficialReleaseSource) -> bool:
    return (
        left.event_id == right.event_id
        and left.source_kind == right.source_kind
        and left.source_url == right.source_url
        and left.source_title == right.source_title
    )


def _symbol(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().upper())
