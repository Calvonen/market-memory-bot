from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from trading_system.ai_event_analyzer import analysis_from_record
from trading_system.brokers.base import BrokerOrder, broker_order_from_payload, broker_order_payload
from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.models import (
    ComponentAssessment,
    PortfolioState,
    TradeProposal,
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


def _order_payload(order: BrokerOrder) -> dict[str, Any]:
    return broker_order_payload(order)


def _order_from_payload(payload: dict[str, Any]) -> BrokerOrder:
    return broker_order_from_payload(payload)


def _strategy_payload(proposal: TradeProposal) -> dict[str, Any]:
    strategy = proposal.strategy_decision
    if strategy is None:
        raise RuntimeError("broker proposal is missing its exact strategy decision audit")
    return {
        "decision_id": strategy.decision_id,
        "instrument": strategy.instrument,
        "direction": strategy.direction.value,
        "confidence": strategy.confidence,
        "scores": {
            "fundamental": strategy.scores.fundamental,
            "catalyst": strategy.scores.catalyst,
            "technical": strategy.scores.technical,
            "market_memory": strategy.scores.market_memory,
            "news_sentiment": strategy.scores.news_sentiment,
            "total": strategy.scores.total,
        },
        "rationale": list(strategy.rationale),
        "invalidation": list(strategy.invalidation),
        "long_evidence": strategy.long_evidence,
        "short_evidence": strategy.short_evidence,
        "source_event_id": strategy.source_event_id,
        "created_at": strategy.created_at.isoformat(),
    }


def _risk_payload(proposal: TradeProposal) -> dict[str, Any]:
    risk = proposal.risk
    return {
        "decision_id": risk.decision_id,
        "status": risk.status.value,
        "reasons": list(risk.reasons),
        "max_risk_amount": risk.max_risk_amount,
        "max_position_value": risk.max_position_value,
        "max_quantity": risk.max_quantity,
        "max_fractional_notional_usd": risk.max_fractional_notional_usd,
        "reward_risk": risk.reward_risk,
        "created_at": risk.created_at.isoformat(),
        "proposal_id": proposal.proposal_id,
        "mode": proposal.mode.value,
    }


class _LeaseGuardedBroker:
    """Reserve one durable task execution before calling the broker."""

    def __init__(
        self,
        broker: Any,
        begin_attempt: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]],
        complete_attempt: Callable[[str, BrokerOrder], None],
    ) -> None:
        self._broker = broker
        self._begin_attempt = begin_attempt
        self._complete_attempt = complete_attempt
        self.supports_fractional_sizing = bool(
            getattr(broker, "supports_fractional_sizing", False)
        )

    def execute(self, proposal: TradeProposal) -> BrokerOrder:
        execution_token = str(uuid4())
        strategy_payload = _strategy_payload(proposal)
        risk_payload = _risk_payload(proposal)
        attempt = self._begin_attempt(execution_token, strategy_payload, risk_payload)
        status = str(attempt.get("attempt_status") or attempt.get("status") or "").strip()
        can_execute = attempt.get("can_execute") is True
        existing_payload = attempt.get("order_payload")

        if status == "completed":
            if not isinstance(existing_payload, dict):
                raise RuntimeError("completed broker attempt is missing order payload")
            return _order_from_payload(existing_payload)
        if not can_execute:
            raise RuntimeError(
                "canonical broker execution attempt is already in progress or outcome is uncertain"
            )

        order = self._broker.execute(proposal)
        self._complete_attempt(execution_token, order)
        return order


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
        "claim_event_paper_run_for_task_v2",
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


def _begin_broker_attempt(
    paper_runs: SupabasePaperTradeRepository,
    *,
    event_id: str,
    analysis_id: str,
    task_id: str,
    expectation_version: int,
    execution_token: str,
    lease_seconds: int,
    strategy_payload: dict[str, Any],
    risk_payload: dict[str, Any],
) -> dict[str, Any]:
    begin_attempt = getattr(paper_runs, "begin_broker_attempt", None)
    if callable(begin_attempt):
        return begin_attempt(
            event_id=event_id,
            analysis_id=analysis_id,
            task_id=task_id,
            expectation_version=expectation_version,
            claim_token=paper_runs.claim_token,
            execution_token=execution_token,
            lease_seconds=lease_seconds,
            strategy_payload=strategy_payload,
            risk_payload=risk_payload,
        )

    response = paper_runs.client.rpc(
        "begin_event_paper_broker_attempt",
        {
            "input_event_id": event_id,
            "input_analysis_id": analysis_id,
            "input_task_id": task_id,
            "input_expectation_version": expectation_version,
            "input_claim_token": paper_runs.claim_token,
            "input_execution_token": execution_token,
            "input_lease_seconds": max(1, lease_seconds),
            "input_strategy_payload": strategy_payload,
            "input_risk_payload": risk_payload,
        },
    ).execute()
    rows = response.data or []
    if not rows:
        raise RuntimeError("canonical broker execution attempt returned no state")
    return rows[0]


def _complete_broker_attempt(
    paper_runs: SupabasePaperTradeRepository,
    *,
    task_id: str,
    execution_token: str,
    order: BrokerOrder,
) -> None:
    complete_attempt = getattr(paper_runs, "complete_broker_attempt", None)
    payload = _order_payload(order)
    if callable(complete_attempt):
        complete_attempt(
            task_id=task_id,
            execution_token=execution_token,
            order_payload=payload,
        )
        return

    response = paper_runs.client.rpc(
        "complete_event_paper_broker_attempt",
        {
            "input_task_id": task_id,
            "input_execution_token": execution_token,
            "input_order_payload": payload,
        },
    ).execute()
    rows = response.data or []
    if not rows:
        raise RuntimeError("canonical broker execution completion returned no state")


def _guard_pipeline(
    pipeline: PaperTradingPipeline | None,
    *,
    paper_runs: SupabasePaperTradeRepository,
    event_id: str,
    analysis_id: str,
    task_id: str,
    expectation_version: int,
    lease_seconds: int,
) -> PaperTradingPipeline:
    base = pipeline or PaperTradingPipeline()
    guarded_broker = _LeaseGuardedBroker(
        base.broker,
        lambda execution_token, strategy_payload, risk_payload: _begin_broker_attempt(
            paper_runs,
            event_id=event_id,
            analysis_id=analysis_id,
            task_id=task_id,
            expectation_version=expectation_version,
            execution_token=execution_token,
            lease_seconds=lease_seconds,
            strategy_payload=strategy_payload,
            risk_payload=risk_payload,
        ),
        lambda execution_token, order: _complete_broker_attempt(
            paper_runs,
            task_id=task_id,
            execution_token=execution_token,
            order=order,
        ),
    )
    return PaperTradingPipeline(
        strategy_engine=base.strategy_engine,
        risk_engine=base.risk_engine,
        broker=guarded_broker,
        journal=base.journal,
        allow_fractional_sizing=base.allow_fractional_sizing,
    )


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
    The task-bound paper-run lease is claimed before Strategy/Risk work. Immediately
    before broker I/O, one durable broker-attempt row is reserved with the exact
    Strategy and Risk audit so recovery can replay the original authorization.
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

    guarded_pipeline = _guard_pipeline(
        pipeline,
        paper_runs=paper_runs,
        event_id=release_event_id,
        analysis_id=analysis_id,
        task_id=requested_task_id,
        expectation_version=expectation.version,
        lease_seconds=lease_seconds,
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
        pipeline=guarded_pipeline,
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