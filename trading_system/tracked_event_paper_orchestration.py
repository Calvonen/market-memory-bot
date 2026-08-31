from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from trading_system.ai_event_analyzer import analysis_from_record
from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.models import (
    ComponentAssessment,
    PortfolioState,
    TradingMode,
)
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.pipeline import PaperTradingPipeline
from trading_system.post_release_paper import PostReleasePaperResult
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_event_paper_bridge import (
    canonical_release_event_id,
    run_post_release_paper_from_tracked_event,
)
from trading_system.tracked_event_repository import SupabaseTrackedEventRepository
from trading_system.trading_task import TradingTaskState
from trading_system.trading_task_repository import SupabaseTradingTaskRepository


@dataclass(frozen=True)
class TrackedPaperOrchestrationResult:
    status: str
    message: str
    persisted: dict[str, Any] | None = None


def _validate_analysis_row(
    row: dict[str, Any],
    *,
    event_id: str,
    expectation_version: int,
) -> tuple[str, str]:
    analysis_id = str(row.get("id") or "").strip()
    source_document_id = str(row.get("source_document_id") or "").strip()
    row_event_id = str(row.get("event_id") or "").strip()
    try:
        row_version = int(row.get("expectation_version"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("canonical event analysis has invalid expectation version") from exc
    if not analysis_id or not source_document_id:
        raise RuntimeError("canonical event analysis is missing persisted identity")
    if row_event_id != event_id or row_version != expectation_version:
        raise RuntimeError("canonical event analysis identity differs from current expectation")
    return analysis_id, source_document_id


def _claim_is_owned(
    claim: dict[str, Any],
    *,
    analysis_id: str,
    task_id: str,
    claim_token: str,
) -> bool:
    return (
        str(claim.get("analysis_id") or "") == analysis_id
        and str(claim.get("task_id") or "") == task_id
        and str(claim.get("claim_token") or "") == claim_token
    )


def _claim_event_for_task(
    paper_runs: SupabasePaperTradeRepository,
    *,
    event_id: str,
    analysis_id: str,
    task_id: str,
    expectation_version: int,
    lease_seconds: int,
) -> dict[str, Any]:
    """Claim a paper run while atomically validating execution authority/version."""
    claim_for_task = getattr(paper_runs, "claim_event_for_task", None)
    if callable(claim_for_task):
        return claim_for_task(
            event_id=event_id,
            analysis_id=analysis_id,
            task_id=task_id,
            expectation_version=expectation_version,
            lease_seconds=lease_seconds,
            claim_token=paper_runs.claim_token,
        )

    response = paper_runs.client.rpc(
        "claim_event_paper_run_for_task",
        {
            "input_event_id": event_id,
            "input_analysis_id": analysis_id,
            "input_task_id": task_id,
            "input_expectation_version": expectation_version,
            "input_claim_token": paper_runs.claim_token,
            "input_lease_seconds": max(1, lease_seconds),
        },
    ).execute()
    rows = response.data or []
    if not rows:
        raise RuntimeError(f"task-bound paper event claim returned no owner for {event_id}")
    return rows[0]


def _persisted_result(persisted: dict[str, Any]) -> TrackedPaperOrchestrationResult:
    """Project the authoritative database outcome back to the caller."""
    status = str(persisted.get("status") or "").strip()
    message = str(persisted.get("message") or "").strip()
    if not status:
        raise RuntimeError("persisted paper result is missing status")
    if not message:
        message = "canonical paper result persisted"
    return TrackedPaperOrchestrationResult(status, message, persisted)


def run_approved_tracked_paper_once(
    *,
    tracked_event_id: str,
    task_id: str,
    tracked_events: SupabaseTrackedEventRepository,
    expectations: SupabaseEventExpectationRepository,
    releases: SupabaseReleaseRepository,
    trading_tasks: SupabaseTradingTaskRepository,
    paper_runs: SupabasePaperTradeRepository,
    resolver: EtoroInstrumentResolver,
    portfolio: PortfolioState,
    lease_seconds: int = 120,
    market_df: pd.DataFrame | None = None,
    technical: ComponentAssessment | None = None,
    market_memory: ComponentAssessment | None = None,
    pipeline: PaperTradingPipeline | None = None,
) -> TrackedPaperOrchestrationResult:
    """Run one canonical tracked earnings event through the existing PAPER path.

    This function creates no execution authority. It requires an already-approved
    canonical PAPER trading task, an already-persisted current release analysis,
    and the persisted tracked-event reaction evidence consumed by the #230 bridge.
    The task-bound paper-run lease is claimed before Strategy/Risk/Broker code so
    concurrent workers cannot both execute the same analysis and the durable run
    can always identify the exact approval that authorized it.
    """
    event_id = tracked_event_id.strip()
    requested_task_id = task_id.strip()
    if not event_id or not requested_task_id:
        raise ValueError("tracked_event_id and task_id must not be blank")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")

    event = tracked_events.get(event_id)
    if event is None:
        raise LookupError("canonical tracked event not found")
    release_event_id = canonical_release_event_id(event)

    expectation = expectations.get(release_event_id)
    if expectation is None:
        return TrackedPaperOrchestrationResult(
            "waiting_analysis",
            "current canonical event expectation is not available",
        )
    if expectation.instrument.strip().upper() != event.instrument.strip().upper():
        raise RuntimeError("current expectation instrument differs from tracked event")

    analysis_row = releases.get_analysis_for_event_version(
        event_id=release_event_id,
        expectation_version=expectation.version,
    )
    if analysis_row is None:
        return TrackedPaperOrchestrationResult(
            "waiting_analysis",
            "current expectation version has no persisted release analysis",
        )
    analysis_id, source_document_id = _validate_analysis_row(
        analysis_row,
        event_id=release_event_id,
        expectation_version=expectation.version,
    )
    try:
        analysis = analysis_from_record(analysis_row)
    except Exception as exc:
        raise RuntimeError("canonical persisted event analysis is malformed") from exc

    task = trading_tasks.get(requested_task_id)
    if task is None:
        raise LookupError("canonical trading task not found")
    if task.task_id != requested_task_id:
        raise RuntimeError("canonical trading task identity differs from requested task")
    if task.tracked_event_id != event.event_id:
        raise RuntimeError("canonical trading task belongs to a different tracked event")
    if task.source_event_id != release_event_id:
        raise RuntimeError("canonical trading task source event differs from tracked event")
    if task.instrument.strip().upper() != event.instrument.strip().upper():
        raise RuntimeError("canonical trading task instrument differs from tracked event")
    if task.mode is not TradingMode.PAPER:
        raise ValueError("canonical trading task does not request PAPER execution")
    if task.state is not TradingTaskState.APPROVED:
        return TrackedPaperOrchestrationResult(
            "waiting_approval",
            "canonical PAPER trading task is not approved",
        )
    execution_context = trading_tasks.execution_context(requested_task_id)

    reactions = tracked_events.list_reactions(event.event_id)

    claim = _claim_event_for_task(
        paper_runs,
        event_id=release_event_id,
        analysis_id=analysis_id,
        task_id=requested_task_id,
        expectation_version=expectation.version,
        lease_seconds=lease_seconds,
    )
    terminal_status = str(claim.get("terminal_status") or "").strip()
    if terminal_status:
        persisted = paper_runs.get_latest_for_event(release_event_id)
        if persisted is not None:
            return _persisted_result(persisted)
        return TrackedPaperOrchestrationResult(
            terminal_status,
            "canonical paper run already has a terminal result",
            claim,
        )
    if not _claim_is_owned(
        claim,
        analysis_id=analysis_id,
        task_id=requested_task_id,
        claim_token=paper_runs.claim_token,
    ):
        return TrackedPaperOrchestrationResult(
            "claim_not_owned",
            "canonical paper execution lease is owned by another runner",
            claim,
        )

    result: PostReleasePaperResult = run_post_release_paper_from_tracked_event(
        event=event,
        expectation=expectation,
        analysis=analysis.payload,
        reactions=reactions,
        portfolio=portfolio,
        resolver=resolver,
        trading_task=execution_context,
        market_df=market_df,
        technical=technical,
        market_memory=market_memory,
        pipeline=pipeline,
    )
    persisted = paper_runs.save_result(
        event_id=release_event_id,
        expectation_version=expectation.version,
        source_document_id=source_document_id,
        analysis_id=analysis_id,
        result=result,
        claim_token=paper_runs.claim_token,
        task_id=requested_task_id,
    )
    return _persisted_result(persisted)
