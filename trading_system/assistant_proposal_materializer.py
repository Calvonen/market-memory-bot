from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from trading_system.assistant_event_proposal import (
    ExistingProposalApproval,
    proposal_approval_via,
    validate_proposal_for_materialization,
)
from trading_system.calendar_release_worker import CalendarReleaseTarget
from trading_system.etoro_instrument_resolver import InstrumentResolutionRequest
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
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


class AssistantProposalReviewedVersionConflict(AssistantProposalMaterializationError):
    pass


@dataclass(frozen=True)
class AssistantProposalMaterializationResult:
    proposal_id: str
    tracked_event_id: str
    event_id: str
    expectation_version: int
    retried: bool


class AssistantProposalMaterializer:
    """Compose an approved proposal into tracking/expectation state only."""

    def __init__(
        self,
        *,
        proposals: Any,
        resolver: Any,
        registry: Any,
        events: Any,
        release_targets: Any,
        expectations: Any,
        official_sources: Any,
        approvals: Any,
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

        is_terminal_retry = proposal.status == "materialized"
        payload = validate_proposal_for_materialization(
            proposal,
            allow_materialized_retry=is_terminal_retry,
        )
        if is_terminal_retry:
            return self._recover_materialized_retry(proposal, payload)

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
        if resolved.instrument_id <= 0:
            raise AssistantProposalInstrumentResolutionError(
                "resolved eToro instrument id must be positive"
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

        try:
            event_write = self.events.register_assistant_proposal_for_tracked_instrument(
                tracked,
                proposal_id=proposal.id,
                expected_base_version=payload.base_expectation_version,
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
        except Exception as exc:
            if "expectation_version_conflict" in str(exc):
                raise AssistantProposalReviewedVersionConflict(str(exc)) from exc
            raise

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
        if self.release_targets.ensure_release_shell(target) != event_id:
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

        if current.version != payload.base_expectation_version:
            raise AssistantProposalReviewedVersionConflict(
                "current expectation version differs from the reviewed proposal base version"
            )

        desired_source, source_version, source_needs_set = self._official_source_for_finalization(
            event_id, payload
        )
        approved_via = proposal_approval_via(proposal)
        fingerprint = draft_fingerprint(normalized)
        retried = False
        try:
            approved = self.approvals.approve_assistant_proposal(
                proposal_id=proposal.id,
                event_id=event_id,
                expected_base_version=payload.base_expectation_version,
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
                official_source=desired_source,
                official_source_expected_version=source_version,
                official_source_needs_set=source_needs_set,
                official_source_actor=_source_audit_actor(proposal),
            )
            version = int(approved.version)
        except OfficialReleaseSourceVersionConflict as exc:
            raise AssistantProposalOfficialSourceConflict(
                "official release source changed during assistant finalization"
            ) from exc
        except ExpectationVersionConflict as exc:
            receipt = self.proposals.find_approval(approved_via=approved_via)
            if receipt is None:
                raise AssistantProposalReviewedVersionConflict(str(exc)) from exc
            version = self._matching_receipt_version(receipt, event_id, fingerprint)
            retried = True

        # The atomic DB finalizer already moves the proposal to materialized.
        # Keep this repository CAS as an idempotent postcondition check so a
        # malformed/non-Supabase test double cannot silently violate the state
        # contract; production sees the already-materialized row and returns.
        self.proposals.mark_materialized(proposal.id)
        return AssistantProposalMaterializationResult(
            proposal.id, tracked_event_id, event_id, version, retried
        )

    def _recover_materialized_retry(self, proposal: Any, payload: Any) -> AssistantProposalMaterializationResult:
        receipt = self.proposals.find_approval(
            approved_via=proposal_approval_via(proposal)
        )
        if receipt is None:
            raise AssistantProposalCanonicalIdentityConflict(
                "materialized proposal has no strategy approval receipt"
            )
        event_id = receipt.event_id.strip()
        if not event_id.startswith("tracked:") or not event_id.removeprefix("tracked:").strip():
            raise AssistantProposalCanonicalIdentityConflict(
                "materialized proposal receipt has invalid tracked event identity"
            )
        fingerprint = draft_fingerprint(normalize_draft(event_id, payload.strategy))
        version = self._matching_receipt_version(receipt, event_id, fingerprint)
        tracked_event_id = event_id.removeprefix("tracked:")
        return AssistantProposalMaterializationResult(
            proposal.id, tracked_event_id, event_id, version, True
        )

    def _official_source_for_finalization(
        self, event_id: str, payload: Any
    ) -> tuple[OfficialReleaseSource, int, bool]:
        desired = OfficialReleaseSource(
            event_id=event_id,
            source_kind=payload.official_source.source_kind,
            source_url=payload.official_source.source_url,
            source_title=payload.official_source.source_title,
        )
        state = self.official_sources.get_state(event_id)
        if state.source is None:
            return desired, state.version, True
        if not _same_source(state.source, desired):
            raise AssistantProposalOfficialSourceConflict(
                "event already has a different approved official release source"
            )
        return desired, state.version, False

    @staticmethod
    def _matching_receipt_version(
        receipt: ExistingProposalApproval, event_id: str, fingerprint: str
    ) -> int:
        if receipt.event_id != event_id or receipt.draft_fingerprint != fingerprint:
            raise AssistantProposalCanonicalIdentityConflict(
                "existing proposal approval receipt does not match this materialization"
            )
        return receipt.expectation_version


def _source_audit_actor(proposal: Any) -> str:
    reviewer = str(proposal.reviewed_by or "").strip()
    actor = f"assistant_proposal:{proposal.id}:reviewer:{reviewer}"
    if not reviewer or "\x00" in actor or len(actor) > 200:
        raise AssistantProposalMaterializationError(
            "assistant proposal reviewer provenance is invalid for official-source audit"
        )
    return actor


def _same_source(left: OfficialReleaseSource, right: OfficialReleaseSource) -> bool:
    return (
        left.event_id,
        left.source_kind,
        left.source_url,
        left.source_title,
    ) == (
        right.event_id,
        right.source_kind,
        right.source_url,
        right.source_title,
    )


def _symbol(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().upper())
