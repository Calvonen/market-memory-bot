from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

import pandas as pd

from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.market_open_evidence import (
    FrozenMarketOpenEvidence,
    _analysis_payload,
    _pattern_from_raw_text,
    freeze_or_load_market_open_evidence,
)
from trading_system.market_open_paper import (
    _validated_opening_reactions,
    detect_market_open_pattern,
    run_market_open_paper,
)
from trading_system.models import ComponentAssessment, PortfolioState, TradeProposal, TradingMode
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.pipeline import PaperTradingPipeline
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_event_paper_bridge import (
    _pipeline_with_task_cap,
    _validate_broker_identity,
    _validate_trading_task_execution,
    canonical_release_event_id,
)
from trading_system.tracked_event_paper_orchestration import (
    TrackedPaperOrchestrationResult,
    _claim_event_for_task,
    _claim_is_owned,
    _guard_pipeline,
    _persisted_result,
)
from trading_system.tracked_event_repository import (
    SupabaseTrackedEventRepository,
    TrackedEventReactionRecord,
)
from trading_system.trading_task import TradingTaskState
from trading_system.trading_task_repository import SupabaseTradingTaskRepository


_MAX_FROZEN_EXECUTION_AGE = timedelta(minutes=2)


class _BrokerBoundaryFreshnessGuard:
    """Fail closed immediately before the durable broker-attempt reservation."""

    def __init__(self, broker: Any, check: Callable[[], None]) -> None:
        self._broker = broker
        self._check = check
        self.supports_fractional_sizing = bool(
            getattr(broker, "supports_fractional_sizing", False)
        )

    def execute(self, proposal: TradeProposal):
        self._check()
        return self._broker.execute(proposal)


def _parse_aware(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(f"frozen market-open {field} is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"frozen market-open {field} is timezone-naive")
    return parsed.astimezone(UTC)


def _frozen_reactions(
    evidence: FrozenMarketOpenEvidence,
    *,
    tracked_event_id: str,
) -> tuple[TrackedEventReactionRecord, ...]:
    try:
        payload = json.loads(evidence.raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("frozen market-open evidence is not valid JSON") from exc
    raw_rows = payload.get("opening_reactions") if isinstance(payload, dict) else None
    if not isinstance(raw_rows, list):
        raise RuntimeError("frozen market-open evidence has no reaction sequence")

    reactions: list[TrackedEventReactionRecord] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise RuntimeError("frozen market-open reaction is malformed")
        reactions.append(
            TrackedEventReactionRecord(
                tracked_market_event_id=tracked_event_id,
                interval_minutes=1,
                candle_start=_parse_aware(raw.get("candle_start"), "candle_start"),
                reference_price=Decimal(str(raw["reference_price"])),
                close_price=Decimal(str(raw["close_price"])),
                return_pct=Decimal(str(raw["return_pct"])),
                direction=str(raw["direction"]),
                evolution="frozen_market_open",
                observed_at=_parse_aware(raw.get("observed_at"), "observed_at"),
            )
        )
    return tuple(reactions)


def _load_existing_evidence(
    releases: SupabaseReleaseRepository,
    *,
    event_id: str,
    expectation_version: int,
    tracked_event_id: str,
    event: Any,
    expectation: Any,
) -> FrozenMarketOpenEvidence | None:
    row = releases.get_analysis_for_event_version(
        event_id=event_id,
        expectation_version=expectation_version,
    )
    if row is None:
        return None
    if str(row.get("provider") or "") != "rule_engine" or str(row.get("model") or "") != "market-open-v1":
        raise RuntimeError("market-open expectation already has a non-market-open analysis")
    source_document_id = str(row.get("source_document_id") or "").strip()
    analysis_id = str(row.get("id") or "").strip()
    if not source_document_id or not analysis_id:
        raise RuntimeError("market-open evidence analysis identity is incomplete")
    docs = (
        releases.client.table("event_source_documents")
        .select("id,event_id,source_type,content_sha256,raw_text")
        .eq("id", source_document_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if len(docs) != 1 or not isinstance(docs[0], dict):
        raise RuntimeError("market-open evidence source document is missing")
    doc = docs[0]
    if str(doc.get("event_id") or "") != event_id or str(doc.get("source_type") or "") != "market_open_reaction_evidence":
        raise RuntimeError("market-open evidence source document identity conflicts")
    raw_text = str(doc.get("raw_text") or "")
    stored_hash = str(doc.get("content_sha256") or "").strip().lower()
    computed_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    if not stored_hash or stored_hash != computed_hash:
        raise RuntimeError("frozen market-open evidence content hash does not match raw text")

    pattern = _pattern_from_raw_text(raw_text, event=event, expectation=expectation)
    persisted_analysis = row.get("analysis")
    if not isinstance(persisted_analysis, dict) or persisted_analysis != _analysis_payload(pattern):
        raise RuntimeError("frozen market-open analysis payload disagrees with reaction evidence")
    if str(row.get("raw_response") or "") != raw_text:
        raise RuntimeError("frozen market-open analysis raw response disagrees with source document")
    return FrozenMarketOpenEvidence(
        analysis_id=analysis_id,
        source_document_id=source_document_id,
        pattern=pattern,
        raw_text=raw_text,
        created=False,
    )


def _execution_evidence_is_fresh(
    *,
    event: Any,
    live_reactions: tuple[TrackedEventReactionRecord, ...],
    frozen_reactions: tuple[TrackedEventReactionRecord, ...],
    evidence: FrozenMarketOpenEvidence,
    now: datetime,
) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("market-open execution clock must be timezone-aware")
    frozen_rows = _validated_opening_reactions(event=event, reactions=frozen_reactions)
    live_rows = _validated_opening_reactions(event=event, reactions=live_reactions)
    if not frozen_rows or not live_rows:
        return False
    frozen_latest = frozen_rows[-1]
    live_latest = live_rows[-1]
    if live_latest.candle_start.astimezone(UTC) != frozen_latest.candle_start.astimezone(UTC):
        return False
    if live_latest.close_price != evidence.pattern.execution_price:
        return False
    completed_at = frozen_latest.candle_start.astimezone(UTC) + timedelta(minutes=1)
    current = now.astimezone(UTC)
    if current < completed_at:
        raise RuntimeError("market-open execution clock precedes confirming candle completion")
    return current <= completed_at + _MAX_FROZEN_EXECUTION_AGE


def _with_broker_boundary_freshness(
    pipeline: PaperTradingPipeline,
    check: Callable[[], None],
) -> PaperTradingPipeline:
    return PaperTradingPipeline(
        strategy_engine=pipeline.strategy_engine,
        risk_engine=pipeline.risk_engine,
        broker=_BrokerBoundaryFreshnessGuard(pipeline.broker, check),
        journal=pipeline.journal,
        allow_fractional_sizing=pipeline.allow_fractional_sizing,
    )


def _terminal_or_unowned_result(
    *,
    claim: dict[str, Any],
    paper_runs: SupabasePaperTradeRepository,
    source_event_id: str,
    analysis_id: str,
    task_id: str,
) -> TrackedPaperOrchestrationResult | None:
    terminal_status = str(claim.get("terminal_status") or "").strip()
    if terminal_status:
        persisted = paper_runs.get_latest_for_event(source_event_id)
        if persisted is not None:
            return _persisted_result(persisted)
        return TrackedPaperOrchestrationResult(
            terminal_status,
            "canonical market-open PAPER run already has a terminal result",
            claim,
        )
    if not _claim_is_owned(
        claim,
        analysis_id=analysis_id,
        task_id=task_id,
        claim_token=paper_runs.claim_token,
    ):
        return TrackedPaperOrchestrationResult(
            "claim_not_owned",
            "canonical PAPER execution lease is owned by another runner",
            claim,
        )
    return None


def _recover_completed_attempt(
    paper_runs: SupabasePaperTradeRepository,
    *,
    source_event_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    response = paper_runs.client.rpc(
        "recover_completed_event_paper_broker_attempt_for_task",
        {
            "input_event_id": source_event_id,
            "input_task_id": task_id,
        },
    ).execute()
    rows = response.data or []
    if len(rows) > 1:
        raise RuntimeError("completed broker-attempt recovery returned ambiguous state")
    return rows[0] if rows else None


def _preflight_new_attempt(paper_runs: SupabasePaperTradeRepository) -> None:
    preflight = getattr(paper_runs, "preflight_new_attempt", None)
    if callable(preflight):
        preflight()


def run_approved_market_open_paper_once(
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
    now: datetime | None = None,
) -> TrackedPaperOrchestrationResult:
    event_key = tracked_event_id.strip()
    requested_task_id = task_id.strip()
    if not event_key or not requested_task_id:
        raise ValueError("tracked_event_id and task_id must not be blank")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")

    event = tracked_events.get(event_key)
    if event is None:
        raise LookupError("canonical tracked event not found")
    if event.kind.strip().lower() != "market_open":
        raise ValueError("canonical tracked event is not market_open")
    if event.calendar_event_id is not None:
        raise ValueError("market_open PAPER requires calendar-less canonical identity")

    source_event_id = canonical_release_event_id(event)

    # Recovery is the first execution-related operation. It is driven only by a
    # durable completed broker attempt and therefore must not depend on current
    # expectation, task state, evidence, prices, Risk, or broker connectivity.
    recovered = _recover_completed_attempt(
        paper_runs,
        source_event_id=source_event_id,
        task_id=requested_task_id,
    )
    if recovered is not None:
        return _persisted_result(recovered)

    expectation = expectations.get(source_event_id)
    if expectation is None:
        return TrackedPaperOrchestrationResult(
            "waiting_analysis",
            "market-open strategy expectation is not available",
        )
    if expectation.instrument.strip().upper() != event.instrument.strip().upper():
        raise RuntimeError("market-open expectation instrument differs from tracked event")

    task = trading_tasks.get(requested_task_id)
    if task is None:
        raise LookupError("canonical trading task not found")
    if task.tracked_event_id != event.event_id:
        raise RuntimeError("canonical trading task belongs to a different tracked event")
    if task.source_event_id != source_event_id:
        raise RuntimeError("canonical trading task source event differs from market-open event")
    if task.mode is not TradingMode.PAPER:
        raise ValueError("canonical trading task does not request PAPER execution")
    if task.state is not TradingTaskState.APPROVED:
        return TrackedPaperOrchestrationResult(
            "waiting_approval",
            "canonical PAPER trading task is not approved",
        )

    execution_context = trading_tasks.execution_context(requested_task_id)
    _validate_trading_task_execution(
        execution_context,
        release_event_id=source_event_id,
        instrument=event.instrument,
    )
    event_cap = execution_context.max_position_value_usd
    if event_cap is None or not math.isfinite(event_cap) or event_cap <= 0:
        return TrackedPaperOrchestrationResult(
            "waiting_approval",
            "market-open PAPER requires a finite positive per-event position cap",
        )
    _validate_broker_identity(event, resolver)

    reactions = tracked_events.list_reactions(event.event_id)
    evidence = _load_existing_evidence(
        releases,
        event_id=source_event_id,
        expectation_version=expectation.version,
        tracked_event_id=event.event_id,
        event=event,
        expectation=expectation,
    )
    if evidence is None:
        pattern = detect_market_open_pattern(event=event, reactions=reactions)
        if pattern is None:
            return TrackedPaperOrchestrationResult(
                "waiting_confirmation",
                "market-open pattern not confirmed from complete opening 1m reactions",
            )
        evidence = freeze_or_load_market_open_evidence(
            releases.client,
            event=event,
            expectation=expectation,
            pattern=pattern,
            reactions=reactions,
        )

    frozen_reactions = _frozen_reactions(evidence, tracked_event_id=event.event_id)
    frozen_pattern = detect_market_open_pattern(event=event, reactions=frozen_reactions)
    if frozen_pattern is None or frozen_pattern.direction is not evidence.pattern.direction:
        raise RuntimeError("frozen market-open evidence no longer reproduces its persisted direction")
    if frozen_pattern.execution_price != evidence.pattern.execution_price:
        raise RuntimeError("frozen market-open evidence no longer reproduces its execution price")

    claim = _claim_event_for_task(
        paper_runs,
        event_id=source_event_id,
        analysis_id=evidence.analysis_id,
        task_id=requested_task_id,
        expectation_version=expectation.version,
        lease_seconds=lease_seconds,
    )
    claimed = _terminal_or_unowned_result(
        claim=claim,
        paper_runs=paper_runs,
        source_event_id=source_event_id,
        analysis_id=evidence.analysis_id,
        task_id=requested_task_id,
    )
    if claimed is not None:
        return claimed

    # Any network broker-access preflight for a genuinely new attempt completes
    # before the freshness clocks below. Recovery above never reaches this call.
    _preflight_new_attempt(paper_runs)

    execution_now = now or datetime.now(UTC)
    if not _execution_evidence_is_fresh(
        event=event,
        live_reactions=tracked_events.list_reactions(event.event_id),
        frozen_reactions=frozen_reactions,
        evidence=evidence,
        now=execution_now,
    ):
        return TrackedPaperOrchestrationResult(
            "waiting_confirmation",
            "frozen market-open execution evidence expired or was superseded by a newer 1m reaction",
        )

    def recheck_before_broker_attempt() -> None:
        boundary_now = now or datetime.now(UTC)
        if not _execution_evidence_is_fresh(
            event=event,
            live_reactions=tracked_events.list_reactions(event.event_id),
            frozen_reactions=frozen_reactions,
            evidence=evidence,
            now=boundary_now,
        ):
            raise RuntimeError(
                "market-open execution evidence expired or was superseded before broker attempt"
            )

    capped_pipeline = _pipeline_with_task_cap(pipeline, execution_context)
    guarded_pipeline = _guard_pipeline(
        capped_pipeline,
        paper_runs=paper_runs,
        event_id=source_event_id,
        analysis_id=evidence.analysis_id,
        task_id=requested_task_id,
        expectation_version=expectation.version,
        lease_seconds=lease_seconds,
    )
    guarded_pipeline = _with_broker_boundary_freshness(
        guarded_pipeline,
        recheck_before_broker_attempt,
    )
    result = run_market_open_paper(
        event=event,
        expectation=expectation,
        reactions=frozen_reactions,
        portfolio=portfolio,
        market_df=market_df,
        technical=technical,
        market_memory=market_memory,
        pipeline=guarded_pipeline,
    )
    persisted = paper_runs.save_result(
        event_id=source_event_id,
        expectation_version=expectation.version,
        source_document_id=evidence.source_document_id,
        analysis_id=evidence.analysis_id,
        result=result,
        claim_token=paper_runs.claim_token,
        task_id=requested_task_id,
    )
    return _persisted_result(persisted)
