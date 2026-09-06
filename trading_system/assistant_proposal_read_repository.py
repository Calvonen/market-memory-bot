from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

VISIBLE_STATUSES = (
    "ready_for_review",
    "approved_for_materialization",
    "materialized",
)
ALL_STATUSES = (
    "draft",
    "ready_for_review",
    "rejected",
    "approved_for_materialization",
    "materialized",
)


@dataclass(frozen=True)
class AssistantProposalMaterializationSummary:
    event_id: str
    tracked_event_id: str
    expectation_version: int


@dataclass(frozen=True)
class AssistantProposalReadRecord:
    id: str
    proposal_key: str
    payload: dict[str, Any]
    requested_execution_mode: str
    status: str
    reviewed_by: str | None
    review_round: int
    created_at: str
    updated_at: str
    materialization: AssistantProposalMaterializationSummary | None = None


def _record(row: dict[str, Any]) -> AssistantProposalReadRecord:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("assistant proposal payload is not an object")
    try:
        review_round = int(row["review_round"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("assistant proposal review_round is invalid") from exc
    if review_round < 0:
        raise RuntimeError("assistant proposal review_round is invalid")
    return AssistantProposalReadRecord(
        id=str(row["id"]),
        proposal_key=str(row["proposal_key"]),
        payload=dict(payload),
        requested_execution_mode=str(row["requested_execution_mode"]),
        status=str(row["status"]),
        reviewed_by=(str(row["reviewed_by"]) if row.get("reviewed_by") else None),
        review_round=review_round,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def canonical_tracked_receipt_identity(raw_event_id: str) -> tuple[str, str]:
    event_id = raw_event_id.strip()
    if not event_id.startswith("tracked:"):
        raise RuntimeError("materialized assistant proposal receipt event id is invalid")
    raw_tracked_event_id = event_id.removeprefix("tracked:").strip()
    try:
        tracked_event_id = str(UUID(raw_tracked_event_id))
    except (ValueError, AttributeError) as exc:
        raise RuntimeError(
            "materialized assistant proposal receipt event id is invalid"
        ) from exc
    return f"tracked:{tracked_event_id}", tracked_event_id


class SupabaseAssistantProposalReadRepository:
    TABLE = "assistant_event_proposals"
    SELECT = (
        "id,proposal_key,payload,requested_execution_mode,status,reviewed_by,"
        "review_round,created_at,updated_at"
    )

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseAssistantProposalReadRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def get(self, proposal_id: str) -> AssistantProposalReadRecord | None:
        response = (
            self.client.table(self.TABLE)
            .select(self.SELECT)
            .eq("id", proposal_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("assistant proposal read returned invalid data")
        record = _record(rows[0])
        if record.status != "materialized":
            return record
        materialization = self._materialization_summary(record.id)
        return AssistantProposalReadRecord(
            id=record.id,
            proposal_key=record.proposal_key,
            payload=record.payload,
            requested_execution_mode=record.requested_execution_mode,
            status=record.status,
            reviewed_by=record.reviewed_by,
            review_round=record.review_round,
            created_at=record.created_at,
            updated_at=record.updated_at,
            materialization=materialization,
        )

    def _materialization_summary(
        self, proposal_id: str
    ) -> AssistantProposalMaterializationSummary:
        approved_via = f"assistant_proposal:{proposal_id}"
        response = (
            self.client.table("event_strategy_approvals")
            .select("event_id,expectation_version")
            .eq("approved_via", approved_via)
            .order("created_at")
            .limit(2)
            .execute()
        )
        rows = response.data or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError(
                "materialized assistant proposal approval receipt is missing or ambiguous"
            )
        event_id, tracked_event_id = canonical_tracked_receipt_identity(
            str(rows[0].get("event_id") or "")
        )
        try:
            expectation_version = int(rows[0]["expectation_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "materialized assistant proposal receipt expectation version is invalid"
            ) from exc
        if expectation_version < 1:
            raise RuntimeError(
                "materialized assistant proposal receipt expectation version is invalid"
            )
        return AssistantProposalMaterializationSummary(
            event_id=event_id,
            tracked_event_id=tracked_event_id,
            expectation_version=expectation_version,
        )

    def list(self, *, status: str | None = None, limit: int = 50) -> list[AssistantProposalReadRecord]:
        if status is not None and status not in ALL_STATUSES:
            raise ValueError("invalid assistant proposal status")
        query = self.client.table(self.TABLE).select(self.SELECT)
        query = query.eq("status", status) if status is not None else query.in_("status", list(VISIBLE_STATUSES))
        response = query.order("created_at", desc=True).limit(limit).execute()
        rows = response.data or []
        if not all(isinstance(row, dict) for row in rows):
            raise RuntimeError("assistant proposal list returned invalid data")
        return [_record(row) for row in rows]
