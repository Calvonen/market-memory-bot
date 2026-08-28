from __future__ import annotations

from typing import Any

from trading_system.event_workflow_readiness import (
    WorkflowExecutionOutcome,
    WorkflowReadinessEvidence,
)
from trading_system.tracked_event_repository import PersistentTrackedEvent


_RELEASE_ERROR_STATUSES = frozenset({"error"})
_ACCEPTED_ORDER_STATUSES = frozenset({"accepted", "pending", "open", "submitted"})
_FILLED_ORDER_STATUSES = frozenset({"filled", "executed", "complete", "completed"})
_REJECTED_ORDER_STATUSES = frozenset({"rejected", "cancelled", "canceled"})
_FAILED_ORDER_STATUSES = frozenset({"failed", "error"})


class SupabaseWorkflowReadinessEvidenceLoader:
    """Load producer-neutral workflow evidence from canonical persisted state.

    The loader reads only durable facts. It does not infer progress from UI state,
    event source, clock time, or calendar ownership. Release identity follows the
    canonical release-shell contract introduced for tracked events.
    """

    def __init__(self, client: Any) -> None:
        self.client = client

    def load(self, event: PersistentTrackedEvent) -> WorkflowReadinessEvidence:
        if not isinstance(event, PersistentTrackedEvent):
            raise ValueError("event must be a PersistentTrackedEvent")

        release_event_id = canonical_release_event_id(event)
        latest_run = self._latest_release_run(release_event_id)
        paper_state = self._paper_state(release_event_id)

        return WorkflowReadinessEvidence(
            tracked_status=event.status,
            release_document_present=self._exists(
                "event_source_documents", "event_id", release_event_id
            ),
            release_failed=_release_requires_action(latest_run),
            analysis_present=self._exists(
                "event_ai_analyses", "event_id", release_event_id
            ),
            reaction_present=self._exists(
                "tracked_market_event_reactions",
                "tracked_market_event_id",
                event.event_id,
            ),
            strategy_present=isinstance((paper_state or {}).get("strategy"), dict),
            risk_present=isinstance((paper_state or {}).get("risk"), dict),
            execution_outcome=_execution_outcome_from_paper_state(paper_state),
        )

    def _exists(self, table: str, field: str, value: str) -> bool:
        response = (
            self.client.table(table)
            .select("id")
            .eq(field, value)
            .limit(1)
            .execute()
        )
        return bool(response.data or [])

    def _latest_release_run(self, event_id: str) -> dict[str, Any] | None:
        response = (
            self.client.table("event_ingestion_runs")
            .select("status,error_message,created_at")
            .eq("event_id", event_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    def _paper_state(self, event_id: str) -> dict[str, Any] | None:
        response = self.client.rpc(
            "get_event_paper_trade_state",
            {"input_event_id": event_id},
        ).execute()
        rows = response.data or []
        return rows[0] if rows else None


def canonical_release_event_id(event: PersistentTrackedEvent) -> str:
    calendar_event_id = (event.calendar_event_id or "").strip()
    if calendar_event_id:
        return f"calendar:{calendar_event_id}"
    event_id = event.event_id.strip()
    if not event_id:
        raise ValueError("tracked event id must not be blank")
    return f"tracked:{event_id}"


def _release_requires_action(run: dict[str, Any] | None) -> bool:
    if run is None:
        return False
    status = str(run.get("status") or "").strip().lower()
    if status in _RELEASE_ERROR_STATUSES:
        return True
    if status != "no_release":
        return False
    message = str(run.get("error_message") or "").lower()
    # no_release is expected before the grace window. Only the durable overdue
    # audit marker escalates it to action_required; provider no-match text alone
    # is not enough to claim the release can no longer arrive normally.
    return "release overdue:" in message


def _execution_outcome_from_paper_state(
    row: dict[str, Any] | None,
) -> WorkflowExecutionOutcome:
    if row is None:
        return WorkflowExecutionOutcome.NOT_STARTED

    status = str(row.get("status") or "").strip().lower()
    if status in {"", "waiting_confirmation"}:
        return WorkflowExecutionOutcome.NOT_STARTED
    if status == "expired_no_trade":
        return WorkflowExecutionOutcome.NO_TRADE
    if status != "paper_executed":
        raise RuntimeError(f"unsupported persisted paper status: {status}")

    order = row.get("paper_order")
    if not isinstance(order, dict):
        raise RuntimeError("paper_executed row is missing paper_order")
    order_status = str(order.get("status") or "").strip().lower()
    if order_status in _FILLED_ORDER_STATUSES:
        return WorkflowExecutionOutcome.FILLED
    if order_status in _ACCEPTED_ORDER_STATUSES:
        return WorkflowExecutionOutcome.ACCEPTED
    if order_status in _REJECTED_ORDER_STATUSES:
        return WorkflowExecutionOutcome.REJECTED
    if order_status in _FAILED_ORDER_STATUSES:
        return WorkflowExecutionOutcome.FAILED
    raise RuntimeError(f"unsupported persisted paper order status: {order_status}")
