from __future__ import annotations

from datetime import date
from typing import Any

from trading_system.models import utc_now
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceVersionConflict,
)
from trading_system.strategy_draft_repository import (
    ExpectationVersionConflict,
    StrategyDraftApprovalResult,
    StrategyDraftEventNotFound,
    SupabaseStrategyDraftApprovalRepository,
)


class SupabaseAssistantStrategyDraftApprovalRepository:
    """Assistant-only finalizer serialized with re-review and source approval."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def approve_assistant_proposal(
        self,
        *,
        proposal_id: str,
        expected_review_round: int,
        event_id: str,
        expected_base_version: int,
        source_name: str | None,
        source_url: str | None,
        source_as_of: date | None,
        consensus: dict[str, Any],
        important_kpis: list[str],
        bull_case: list[str],
        base_case: list[str],
        bear_case: list[str],
        triggers: dict[str, Any],
        invalidation_conditions: list[str],
        change_note: str,
        draft_fingerprint: str,
        approved_by: str,
        approved_via: str,
        official_source: OfficialReleaseSource,
        official_source_expected_version: int,
        official_source_needs_set: bool,
        official_source_actor: str,
    ) -> StrategyDraftApprovalResult:
        if expected_review_round < 1:
            raise ValueError("expected_review_round must be positive")
        params = {
            "input_proposal_id": proposal_id,
            "input_expected_review_round": expected_review_round,
            "input_event_id": event_id,
            "input_expected_base_version": expected_base_version,
            "input_source_name": source_name,
            "input_source_url": source_url,
            "input_source_as_of": source_as_of.isoformat() if source_as_of else None,
            "input_consensus": consensus,
            "input_important_kpis": list(important_kpis),
            "input_bull_case": list(bull_case),
            "input_base_case": list(base_case),
            "input_bear_case": list(bear_case),
            "input_triggers": triggers,
            "input_invalidation_conditions": list(invalidation_conditions),
            "input_change_note": change_note,
            "input_draft_fingerprint": draft_fingerprint,
            "input_approved_by": approved_by,
            "input_approved_via": approved_via,
            "input_official_source_kind": official_source.source_kind,
            "input_official_source_url": official_source.source_url,
            "input_official_source_title": official_source.source_title,
            "input_official_source_expected_version": official_source_expected_version,
            "input_official_source_needs_set": official_source_needs_set,
            "input_official_source_actor": official_source_actor,
        }
        try:
            response = self.client.rpc(
                "approve_assistant_proposal_strategy_draft", params
            ).execute()
        except Exception as exc:
            if SupabaseStrategyDraftApprovalRepository._is_version_conflict(exc):
                raise ExpectationVersionConflict(str(exc)) from exc
            message = getattr(exc, "message", None)
            message_text = str(message) if message is not None else str(exc)
            if getattr(exc, "code", None) == "40001" and (
                "version_conflict:" in message_text
                or "assistant_proposal_official_source_state_conflict" in message_text
            ):
                raise OfficialReleaseSourceVersionConflict(
                    "official release source changed during assistant finalization"
                ) from exc
            if SupabaseStrategyDraftApprovalRepository._is_event_not_found(exc):
                raise StrategyDraftEventNotFound(event_id) from exc
            raise

        rows = response.data or []
        if not rows:
            raise RuntimeError("approve_assistant_proposal_strategy_draft returned no rows")
        row = rows[0]
        return StrategyDraftApprovalResult(
            version=int(row["new_version"]),
            updated_at=(
                SupabaseStrategyDraftApprovalRepository._parse_datetime(row.get("created_at"))
                or utc_now()
            ),
        )
