from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.market_open_evidence import (
    FrozenMarketOpenEvidence,
    freeze_or_load_market_open_evidence,
)
from trading_system.market_open_paper import detect_market_open_pattern, run_market_open_paper
from trading_system.models import ComponentAssessment, PortfolioState, TradingMode
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
        .select("id,event_id,source_type,raw_text")
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

    pattern_payload = json.loads(raw_text).get("pattern")
    if not isinstance(pattern_payload, dict):
        raise RuntimeError("market-open evidence pattern is missing")
    from trading_system.market_open_evidence import _pattern_from_raw_text

    pattern = _pattern_from_raw_text(raw_text, event=event, expectation=expectation)
    return FrozenMarketOpenEvidence(
        analysis_id=analysis_id,
        source_document_id=source_document_id,
        pattern=pattern,
        raw_text=raw_text,
        created=False,
    )


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
        analysis_id=evidence.analysis_id,
        task_id=requested_task_id,
        claim_token=paper_runs.claim_token,
    ):
        return TrackedPaperOrchestrationResult(
            "claim_not_owned",
            "canonical PAPER execution lease is owned by another runner",
            claim,
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
