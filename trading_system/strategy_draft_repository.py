from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


class StrategyApprovalAuditRepository(Protocol):
    """Audit trail for strategy-draft approvals.

    Every successful approve() call must be recorded here in addition to the
    new EventExpectation version itself: who/what approved, when, which
    draft fingerprint, and which expectation version the approval was based
    on. This is separate from event_expectation_versions (which only knows
    the resulting snapshot, not the approval act that produced it).
    """

    def record(
        self,
        *,
        event_id: str,
        expectation_version: int,
        base_expectation_version: int,
        draft_fingerprint: str,
        approved_by: str,
        approved_via: str,
        change_note: str,
    ) -> None: ...


@dataclass
class InMemoryStrategyApprovalAuditRepository:
    records: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        *,
        event_id: str,
        expectation_version: int,
        base_expectation_version: int,
        draft_fingerprint: str,
        approved_by: str,
        approved_via: str,
        change_note: str,
    ) -> None:
        self.records.append(
            {
                "event_id": event_id,
                "expectation_version": expectation_version,
                "base_expectation_version": base_expectation_version,
                "draft_fingerprint": draft_fingerprint,
                "approved_by": approved_by,
                "approved_via": approved_via,
                "change_note": change_note,
                "created_at": datetime.now(UTC),
            }
        )


class SupabaseStrategyApprovalAuditRepository:
    """Backed by the `event_strategy_approvals` table (service-role only,
    see supabase/migrations). Insert-only: this is an audit log, never
    edited or read back by application code."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseStrategyApprovalAuditRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def record(
        self,
        *,
        event_id: str,
        expectation_version: int,
        base_expectation_version: int,
        draft_fingerprint: str,
        approved_by: str,
        approved_via: str,
        change_note: str,
    ) -> None:
        self.client.table("event_strategy_approvals").insert(
            {
                "event_id": event_id,
                "expectation_version": expectation_version,
                "base_expectation_version": base_expectation_version,
                "draft_fingerprint": draft_fingerprint,
                "approved_by": approved_by,
                "approved_via": approved_via,
                "change_note": change_note,
            }
        ).execute()
