from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from trading_system.calendar_release_worker import (
    CalendarReleaseTarget,
    SupabaseCalendarReleaseTargetRepository,
)
from trading_system.canonical_tracked_event_ingress import (
    SupabaseCanonicalTrackedEventIngress,
)
from trading_system.etoro_instrument_resolver import (
    EtoroInstrumentResolver,
    InstrumentResolutionRequest,
    ResolvedEtoroInstrument,
)
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    SupabaseOfficialReleaseSourceRepository,
)
from trading_system.strategy_draft import (
    StrategyDraftPayload,
    draft_fingerprint,
    identity_mismatches,
    normalize_draft,
)
from trading_system.strategy_draft_repository import (
    ExpectationVersionConflict,
    SupabaseStrategyDraftApprovalRepository,
)
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_event_repository import TrackedEventTimeStatus
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.tracked_instrument_registry import SupabaseTrackedInstrumentRegistry


class AssistantProposalNotReady(RuntimeError):
    pass


class AssistantProposalLiveLocked(RuntimeError):
    pass


class AssistantProposalIdentityConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class AssistantEventProposalRecord:
    id: str
    proposal_key: str
    payload: dict[str, Any]
    requested_execution_mode: str
    status: str
    reviewed_by: str | None


@dataclass(frozen=True)
class ExistingProposalApproval:
    event_id: str
    expectation_version: int
    draft_fingerprint: str


@dataclass(frozen=True)
class AssistantProposalMaterializationResult:
    proposal_id: str
    tracked_event_id: str
    event_id: str
    expectation_version: int
    action: str


class OfficialSourcePayload(BaseModel):
    source_kind: Literal["direct_url", "results_page"]
    source_url: str = Field(min_length=1, max_length=2000)
    source_title: str | None = Field(default=None, max_length=500)


class AssistantEventProposalPayload(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    instrument: str = Field(min_length=1, max_length=32)
    market: str = Field(min_length=1, max_length=64)
    kind: Literal["earnings"] = "earnings"
    title: str = Field(min_length=1, max_length=200)
    scheduled_date: date
    event_at: datetime
    event_time_status: Literal["confirmed", "estimated"]
    official_source: OfficialSourcePayload
    strategy: StrategyDraftPayload

    @field_validator("company_name", "instrument", "market", "title", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("event_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event_at must be timezone-aware")
        return value


class ProposalInstrumentResolver(Protocol):
    def resolve(self, request: InstrumentResolutionRequest) -> ResolvedEtoroInstrument | None: ...


class SupabaseAssistantEventProposalRepository:
    TABLE = "assistant_event_proposals"

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseAssistantEventProposalRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def get(self, proposal_id: str) -> AssistantEventProposalRecord | None:
        response = (
            self.client.table(self.TABLE)
            .select(
                "id,proposal_key,payload,requested_execution_mode,status,reviewed_by"
            )
            .eq("id", proposal_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("assistant proposal read returned invalid data")
        row = rows[0]
        payload = row.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("assistant proposal payload is not an object")
        return AssistantEventProposalRecord(
            id=str(row["id"]),
            proposal_key=str(row["proposal_key"]),
            payload=dict(payload),
            requested_execution_mode=str(row["requested_execution_mode"]),
            status=str(row["status"]),
            reviewed_by=(str(row["reviewed_by"]) if row.get("reviewed_by") else None),
        )

    def find_approval(self, *, approved_via: str) -> ExistingProposalApproval | None:
        response = (
            self.client.table("event_strategy_approvals")
            .select("event_id,expectation_version,draft_fingerprint")
            .eq("approved_via", approved_via)
            .order("created_at")
            .limit(2)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("assistant proposal has multiple strategy approvals")
        row = rows[0]
        return ExistingProposalApproval(
            event_id=str(row["event_id"]),
            expectation_version=int(row["expectation_version"]),
            draft_fingerprint=str(row["draft_fingerprint"]),
        )

    def mark_materialized(self, proposal_id: str) -> None:
        response = (
            self.client.table(self.TABLE)
            .update({"status": "materialized"})
            .eq("id", proposal_id)
            .eq("status", "approved_for_materialization")
            .execute()
        )
        rows = response.data or []
        if rows:
            return
        current = self.get(proposal_id)
        if current is None or current.status != "materialized":
            raise RuntimeError("assistant proposal materialized status CAS failed")


class AssistantEventProposalMaterializer:
    """Turn one user-approved proposal into canonical tracking state.

    This boundary never creates trading tasks and never grants PAPER/DEMO or
    LIVE execution authority. LIVE proposals fail closed. Strategy persistence
    reuses the existing atomic strategy-draft approval RPC and its audit trail;
    the deterministic ``approved_via`` value is also the retry receipt.
    """

    def __init__(
        self,
        *,
        proposals: SupabaseAssistantEventProposalRepository,
        resolver: ProposalInstrumentResolver,
        instruments: SupabaseTrackedInstrumentRegistry,
        events: SupabaseCanonicalTrackedEventIngress,
        release_shells: SupabaseCalendarReleaseTargetRepository,
        expectations: SupabaseEventExpectationRepository,
        official_sources: SupabaseOfficialReleaseSourceRepository,
        approvals: SupabaseStrategyDraftApprovalRepository,
    ) -> None:
        self.proposals = proposals
        self.resolver = resolver
        self.instruments = instruments
        self.events = events
        self.release_shells = release_shells
        self.expectations = expectations
        self.official_sources = official_sources
        self.approvals = approvals

    def materialize(self, proposal_id: str) -> AssistantProposalMaterializationResult:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise AssistantProposalNotReady("assistant proposal not found")

        approved_via = f"assistant_proposal:{proposal.id}"
        existing = self.proposals.find_approval(approved_via=approved_via)
        if proposal.status == "materialized":
            if existing is None:
                raise RuntimeError("materialized assistant proposal is missing approval audit")
            return AssistantProposalMaterializationResult(
                proposal_id=proposal.id,
                tracked_event_id=self._tracked_id_from_event_id(existing.event_id),
                event_id=existing.event_id,
                expectation_version=existing.expectation_version,
                action="already_materialized",
            )

        if proposal.status != "approved_for_materialization":
            raise AssistantProposalNotReady(
                f"assistant proposal status is {proposal.status}, not approved_for_materialization"
            )
        if proposal.requested_execution_mode != "demo":
            raise AssistantProposalLiveLocked(
                "LIVE proposal materialization is locked; only demo proposals are accepted"
            )
        if not proposal.reviewed_by or not proposal.reviewed_by.strip():
            raise AssistantProposalNotReady("approved proposal is missing reviewer identity")

        payload = AssistantEventProposalPayload.model_validate(proposal.payload)
        self._validate_draft_outer_identity(payload)

        resolved = self.resolver.resolve(
            InstrumentResolutionRequest(
                instrument=payload.instrument,
                company_name=payload.company_name,
                market=payload.market,
            )
        )
        if resolved is None:
            raise AssistantProposalIdentityConflict(
                "proposal instrument could not be resolved uniquely through eToro"
            )

        tracked_record = self.instruments.upsert(
            instrument=payload.instrument,
            company_name=payload.company_name,
            market=payload.market,
            source="manual",
            actor="assistant_proposal_materializer",
        )
        self._validate_registry_identity(payload, tracked_record.instrument, tracked_record.market)
        if not tracked_record.active:
            raise AssistantProposalIdentityConflict("canonical tracked instrument is inactive")

        tracked = TrackedEtoroInstrument(
            tracked_instrument_id=tracked_record.id,
            instrument=tracked_record.instrument,
            market=tracked_record.market,
            etoro_instrument_id=resolved.instrument_id,
            etoro_symbol=resolved.symbol,
            etoro_display_name=resolved.display_name,
            etoro_market=resolved.market,
        )
        event_write = self.events.register_for_tracked_instrument(
            tracked,
            company_name=payload.company_name,
            source="assistant_proposal",
            external_key=proposal.proposal_key,
            kind=payload.kind,
            title=payload.title,
            event_at=payload.event_at,
            event_date=payload.scheduled_date,
            event_time_status=TrackedEventTimeStatus(payload.event_time_status),
            actor="assistant_proposal_materializer",
        )

        expected_event_id = f"tracked:{event_write.event_id}"
        event_id = self.release_shells.ensure_release_shell(
            CalendarReleaseTarget(
                calendar_event_id=None,
                event_id=expected_event_id,
                ticker=tracked.instrument,
                scheduled_date=payload.scheduled_date,
                market=tracked.market,
                tracked_event_id=event_write.event_id,
            )
        )
        current = self.expectations.get(event_id)
        if current is None:
            raise RuntimeError("canonical release shell has no expectation")

        normalized = normalize_draft(event_id, payload.strategy)
        mismatches = identity_mismatches(normalized, current)
        if mismatches:
            raise AssistantProposalIdentityConflict("; ".join(mismatches))
        fingerprint = draft_fingerprint(normalized)

        existing = self.proposals.find_approval(approved_via=approved_via)
        if existing is not None:
            self._validate_existing_approval(existing, event_id, fingerprint)
            self.proposals.mark_materialized(proposal.id)
            return AssistantProposalMaterializationResult(
                proposal_id=proposal.id,
                tracked_event_id=event_write.event_id,
                event_id=event_id,
                expectation_version=existing.expectation_version,
                action="recovered_retry",
            )

        desired_source = OfficialReleaseSource(
            event_id=event_id,
            source_kind=payload.official_source.source_kind,
            source_url=payload.official_source.source_url,
            source_title=payload.official_source.source_title,
        )
        source_state = self.official_sources.get_state(event_id)
        if source_state.source is None:
            self.official_sources.set(
                desired_source,
                expected_version=source_state.version,
                actor=proposal.reviewed_by,
            )
        else:
            self._validate_official_source(source_state.source, desired_source)

        try:
            approval = self.approvals.approve(
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
                approved_by=proposal.reviewed_by,
                approved_via=approved_via,
            )
            expectation_version = approval.version
            action = "materialized"
        except ExpectationVersionConflict:
            existing = self.proposals.find_approval(approved_via=approved_via)
            if existing is None:
                raise
            self._validate_existing_approval(existing, event_id, fingerprint)
            expectation_version = existing.expectation_version
            action = "recovered_concurrent_retry"

        self.proposals.mark_materialized(proposal.id)
        return AssistantProposalMaterializationResult(
            proposal_id=proposal.id,
            tracked_event_id=event_write.event_id,
            event_id=event_id,
            expectation_version=expectation_version,
            action=action,
        )

    @staticmethod
    def _validate_draft_outer_identity(payload: AssistantEventProposalPayload) -> None:
        if _symbol(payload.strategy.instrument) != _symbol(payload.instrument):
            raise AssistantProposalIdentityConflict(
                "strategy instrument differs from proposal instrument"
            )
        if payload.strategy.event_name.strip() != payload.title.strip():
            raise AssistantProposalIdentityConflict(
                "strategy event_name differs from proposal title"
            )
        if payload.strategy.scheduled_date != payload.scheduled_date:
            raise AssistantProposalIdentityConflict(
                "strategy scheduled_date differs from proposal scheduled_date"
            )

    @staticmethod
    def _validate_registry_identity(
        payload: AssistantEventProposalPayload,
        instrument: str,
        market: str,
    ) -> None:
        if _symbol(instrument) != _symbol(payload.instrument):
            raise AssistantProposalIdentityConflict(
                "tracked-instrument registry returned a different instrument"
            )
        if _market(market) != _market(payload.market):
            raise AssistantProposalIdentityConflict(
                "tracked-instrument registry returned a different market"
            )

    @staticmethod
    def _validate_existing_approval(
        approval: ExistingProposalApproval,
        event_id: str,
        fingerprint: str,
    ) -> None:
        if approval.event_id != event_id or approval.draft_fingerprint != fingerprint:
            raise AssistantProposalIdentityConflict(
                "existing proposal approval does not match this canonical event/draft"
            )

    @staticmethod
    def _validate_official_source(
        existing: OfficialReleaseSource,
        desired: OfficialReleaseSource,
    ) -> None:
        if (
            existing.source_kind != desired.source_kind
            or existing.source_url != desired.source_url
            or existing.source_title != desired.source_title
        ):
            raise AssistantProposalIdentityConflict(
                "canonical event already has a different approved official source"
            )

    @staticmethod
    def _tracked_id_from_event_id(event_id: str) -> str:
        prefix = "tracked:"
        if not event_id.startswith(prefix) or not event_id[len(prefix) :]:
            raise RuntimeError("assistant proposal approval has invalid tracked event identity")
        return event_id[len(prefix) :]


def _symbol(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().upper())


def _market(value: str) -> str:
    return " ".join(value.strip().upper().split())
