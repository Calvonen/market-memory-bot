from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.strategy_draft import StrategyDraftPayload


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


class OfficialSourcePayload(BaseModel):
    source_kind: Literal["direct_url", "results_page"]
    source_url: str = Field(min_length=1, max_length=2000)
    source_title: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _validate_canonical_source(self) -> "OfficialSourcePayload":
        # Reuse the canonical release-source validator at the proposal boundary
        # so malformed sources fail before any later materializer side effects.
        OfficialReleaseSource(
            event_id="assistant-proposal-validation",
            source_kind=self.source_kind,
            source_url=self.source_url,
            source_title=self.source_title,
        )
        return self


class AssistantEventProposalPayload(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    instrument: str = Field(min_length=1, max_length=32)
    market: str = Field(min_length=1, max_length=64)
    kind: Literal["earnings"]
    title: str = Field(min_length=1, max_length=200)
    scheduled_date: date
    event_at: datetime
    event_time_status: Literal["confirmed", "estimated", "unknown"]
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
            .select("id,proposal_key,payload,requested_execution_mode,status,reviewed_by")
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
        if response.data:
            return
        current = self.get(proposal_id)
        if current is None or current.status != "materialized":
            raise RuntimeError("assistant proposal materialized status CAS failed")


def validate_proposal_for_materialization(
    proposal: AssistantEventProposalRecord,
) -> AssistantEventProposalPayload:
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
    if _symbol(payload.strategy.instrument) != _symbol(payload.instrument):
        raise AssistantProposalIdentityConflict(
            "strategy instrument differs from proposal instrument"
        )
    if payload.strategy.scheduled_date != payload.scheduled_date:
        raise AssistantProposalIdentityConflict(
            "strategy scheduled_date differs from proposal scheduled_date"
        )
    return payload


def proposal_approval_via(proposal: AssistantEventProposalRecord) -> str:
    return f"assistant_proposal:{proposal.id}"


def _symbol(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().upper())
