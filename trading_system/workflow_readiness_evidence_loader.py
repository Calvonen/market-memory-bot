from __future__ import annotations

from typing import Any

from trading_system.event_workflow_readiness import (
    WorkflowExecutionOutcome,
    WorkflowReadinessEvidence,
)
from trading_system.models import TradingMode
from trading_system.tracked_event_repository import PersistentTrackedEvent


_RELEASE_ERROR_STATUSES = frozenset({"error"})
_CANONICAL_RELEASE_BLOCKER_PROVIDER = "canonical_release_worker"
_ACTION_REQUIRED_PREFIX = "action_required:"
_ACCEPTED_ORDER_STATUSES = frozenset(
    {"accepted", "etoro_demo_accepted", "pending", "open", "submitted"}
)
_FILLED_ORDER_STATUSES = frozenset(
    {"filled", "filled_simulated", "executed", "complete", "completed"}
)
_REJECTED_ORDER_STATUSES = frozenset({"rejected", "cancelled", "canceled"})
_FAILED_ORDER_STATUSES = frozenset({"failed", "error"})


class SupabaseWorkflowReadinessEvidenceLoader:
    """Load producer-neutral workflow evidence from canonical persisted state.

    The loader reads only durable facts. It does not infer progress from UI state,
    event source, clock time, or calendar ownership. Release identity follows the
    canonical release-shell contract introduced for tracked events. Versioned
    downstream evidence is accepted only for the current expectation version.

    Strategy/Risk/execution fields come from ``get_event_paper_trade_state`` and
    are therefore explicitly tagged with PAPER provenance. A LIVE workflow must
    obtain trading evidence from a LIVE-scoped source instead of reusing this
    loader's PAPER state.
    """

    def __init__(self, client: Any) -> None:
        self.client = client

    def load(self, event: PersistentTrackedEvent) -> WorkflowReadinessEvidence:
        if not isinstance(event, PersistentTrackedEvent):
            raise ValueError("event must be a PersistentTrackedEvent")

        release_event_id = canonical_release_event_id(event)
        current_version = self._current_expectation_version(release_event_id)
        latest_run = self._latest_release_run(release_event_id)
        tracked_release_blocker = self._tracked_release_blocker_metadata(event.event_id)
        release_skipped = self._release_skip_present(event.event_id, release_event_id)
        paper_state = self._paper_state_for_version(release_event_id, current_version)
        persisted_release_document_present = self._exists(
            "event_source_documents", "event_id", release_event_id
        )
        analysis_present = self._analysis_exists_for_version(
            release_event_id, current_version
        )
        canonical_blocker = (
            _is_canonical_release_blocker(latest_run)
            or tracked_release_blocker is not None
        )
        release_document_present = (
            persisted_release_document_present and not canonical_blocker
        )
        release_action_code, release_action_reason = _release_action_metadata(
            latest_run,
            tracked_release_blocker,
            release_document_present=release_document_present,
        )
        if release_skipped:
            # An explicit audited skip resolves an outstanding release action
            # without mutating blocker rows. A genuinely persisted canonical
            # release document still wins in the readiness projection.
            release_action_code = None
            release_action_reason = None

        return WorkflowReadinessEvidence(
            tracked_status=event.status,
            event_id=event.event_id,
            release_document_present=release_document_present,
            release_skipped=release_skipped,
            release_failed=release_action_code is not None,
            release_action_code=release_action_code,
            release_action_reason=release_action_reason,
            analysis_present=analysis_present,
            reaction_present=self._exists(
                "tracked_market_event_reactions",
                "tracked_market_event_id",
                event.event_id,
            ),
            strategy_present=isinstance((paper_state or {}).get("strategy"), dict),
            risk_present=isinstance((paper_state or {}).get("risk"), dict),
            execution_outcome=_execution_outcome_from_paper_state(paper_state),
            trading_mode=TradingMode.PAPER,
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

    def _release_skip_present(self, tracked_event_id: str, release_event_id: str) -> bool:
        response = (
            self.client.table("tracked_event_release_skip_audit")
            .select("id")
            .eq("tracked_event_id", tracked_event_id)
            .eq("release_event_id", release_event_id)
            .limit(1)
            .execute()
        )
        return bool(response.data or [])

    def _current_expectation_version(self, event_id: str) -> int | None:
        response = (
            self.client.table("current_event_expectations")
            .select("version")
            .eq("event_id", event_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        value = rows[0].get("version")
        if isinstance(value, bool):
            raise RuntimeError("current expectation version is invalid")
        try:
            version = int(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("current expectation version is invalid") from exc
        if version < 1:
            raise RuntimeError("current expectation version is invalid")
        return version

    def _analysis_exists_for_version(self, event_id: str, version: int | None) -> bool:
        if version is None:
            return False
        response = (
            self.client.table("event_ai_analyses")
            .select("id")
            .eq("event_id", event_id)
            .eq("expectation_version", version)
            .limit(1)
            .execute()
        )
        return bool(response.data or [])

    def _latest_release_run(self, event_id: str) -> dict[str, Any] | None:
        response = (
            self.client.table("event_ingestion_runs")
            .select("provider,status,error_message,created_at")
            .eq("event_id", event_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    def _tracked_release_blocker(self, tracked_event_id: str) -> bool:
        return self._tracked_release_blocker_metadata(tracked_event_id) is not None

    def _tracked_release_blocker_metadata(
        self, tracked_event_id: str
    ) -> dict[str, Any] | None:
        response = (
            self.client.table("tracked_event_workflow_blockers")
            .select("blocker_code,message,resolved_at,updated_at")
            .eq("tracked_market_event_id", tracked_event_id)
            .eq("step_key", "release")
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows or rows[0].get("resolved_at") is not None:
            return None
        return rows[0]

    def _paper_state_for_version(
        self, event_id: str, version: int | None
    ) -> dict[str, Any] | None:
        response = self.client.rpc(
            "get_event_paper_trade_state",
            {"input_event_id": event_id},
        ).execute()
        rows = response.data or []
        if not rows or version is None:
            return None
        row = rows[0]
        value = row.get("expectation_version")
        if isinstance(value, bool):
            raise RuntimeError("persisted paper expectation_version is invalid")
        try:
            persisted_version = int(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("persisted paper expectation_version is invalid") from exc
        if persisted_version != version:
            return None
        return row


def canonical_release_event_id(event: PersistentTrackedEvent) -> str:
    calendar_event_id = (event.calendar_event_id or "").strip()
    if calendar_event_id:
        return f"calendar:{calendar_event_id}"
    event_id = event.event_id.strip()
    if not event_id:
        raise ValueError("tracked event id must not be blank")
    return f"tracked:{event_id}"


def _is_canonical_release_blocker(run: dict[str, Any] | None) -> bool:
    if run is None:
        return False
    provider = str(run.get("provider") or "").strip().lower()
    status = str(run.get("status") or "").strip().lower()
    message = str(run.get("error_message") or "").strip().lower()
    return (
        provider == _CANONICAL_RELEASE_BLOCKER_PROVIDER
        and status == "error"
        and message.startswith(_ACTION_REQUIRED_PREFIX)
    )


def _release_action_metadata(
    run: dict[str, Any] | None,
    tracked_blocker: dict[str, Any] | None,
    *,
    release_document_present: bool,
) -> tuple[str | None, str | None]:
    if tracked_blocker is not None:
        code = str(tracked_blocker.get("blocker_code") or "").strip()
        reason = str(tracked_blocker.get("message") or "").strip()
        if not code or not reason:
            raise RuntimeError("tracked release blocker metadata is invalid")
        return code, reason

    if _is_canonical_release_blocker(run):
        message = str((run or {}).get("error_message") or "").strip()
        reason = message[len(_ACTION_REQUIRED_PREFIX) :].strip()
        if not reason:
            raise RuntimeError("canonical release blocker reason is missing")
        return "release_action_required", reason

    if release_document_present or run is None:
        return None, None

    status = str(run.get("status") or "").strip().lower()
    message = str(run.get("error_message") or "").strip()
    if status in _RELEASE_ERROR_STATUSES:
        return "release_ingestion_error", message or "release ingestion failed"
    if status == "no_release" and "release overdue:" in message.lower():
        return "release_overdue", message
    return None, None


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
