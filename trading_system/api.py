from __future__ import annotations

import math
import os
import re
import secrets
from dataclasses import asdict, replace
from datetime import date
from typing import Any, Protocol


from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from trading_system.calendar_provider import CalendarCandidate
from trading_system.calendar_repository import (
    CalendarEventNotFound,
    CalendarEventRepository,
    CalendarEventStatus,
    InvalidCalendarEventTransition,
)
from trading_system.event_repository import (
    EventExpectationNotFound,
    EventExpectationRepository,
    ExpectationPatch,
)
from trading_system.models import EventExpectation
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.strategy_draft import (
    StrategyDraftPayload,
    changed_fields,
    draft_fingerprint,
    draft_warnings,
    identity_mismatches,
    normalize_draft,
    reject_non_finite_numbers,
)
from trading_system.strategy_draft_repository import (
    ExpectationVersionConflict,
    StrategyDraftApprovalRepository,
    StrategyDraftEventNotFound,
    SupabaseStrategyDraftApprovalRepository,
)
from trading_system.supabase_calendar_repository import SupabaseCalendarEventRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    SupabaseTrackedEventRepository,
)

# Calendar range MVP: the mobile UI only ever offers 7/30-day chips and the
# calendar sync worker never fetches further out than this (see
# docs/calendar_watchlist.md) - GET /api/v1/calendar/upcoming enforces the
# same cap so a caller can never silently request/receive more.
MAX_CALENDAR_LOOKAHEAD_DAYS = 30


class PaperStatusRepository(Protocol):
    def get_latest_for_event(self, event_id: str) -> dict[str, Any] | None: ...


class ExpectationVersionRequest(BaseModel):
    change_note: str = Field(min_length=3, max_length=500)
    consensus: dict[str, float | str | None] | None = None
    important_kpis: list[str] | None = None
    bull_case: list[str] | None = None
    base_case: list[str] | None = None
    bear_case: list[str] | None = None
    triggers: dict[str, float | str] | None = None
    invalidation_conditions: list[str] | None = None
    source_name: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=2000)
    source_as_of: date | None = None

    @field_validator("consensus", "triggers", mode="after")
    @classmethod
    def _reject_non_finite_values(
        cls, value: dict[str, Any] | None, info: ValidationInfo
    ) -> dict[str, Any] | None:
        if value is None:
            return value
        assert info.field_name is not None
        return reject_non_finite_numbers(info.field_name, value)


class StrategyDraftApprovalRequest(BaseModel):
    draft: StrategyDraftPayload
    draft_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    base_expectation_version: int = Field(ge=1)
    approved_by: str = Field(min_length=1, max_length=200)
    approved_via: str | None = Field(default=None, max_length=100)

    @field_validator("draft_fingerprint", mode="before")
    @classmethod
    def _normalize_fingerprint_case(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.lower()
        return value

    @field_validator("approved_by", mode="before")
    @classmethod
    def _strip_approved_by_before_length_check(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


def _analyze_market_ticker(ticker: str) -> dict[str, Any]:
    from market_memory.analysis import analyze_ticker
    return analyze_ticker(ticker)


def _scan_market(market: str, limit: int) -> dict[str, Any]:
    from market_memory.analysis import scan_market
    return scan_market(market, limit)


def _search_market_symbols(query: str, limit: int) -> list[dict[str, str]]:
    from market_memory.data import search_symbols
    return search_symbols(query, limit)


def _expectation_payload(expectation: EventExpectation) -> dict[str, Any]:
    return asdict(expectation)


def _calendar_event_payload(event: Any) -> dict[str, Any]:
    payload = asdict(event)
    payload["status"] = event.status.value
    return payload


def _tracked_event_payload(event: PersistentTrackedEvent) -> dict[str, Any]:
    def iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "event_id": event.event_id,
        "tracked_instrument_id": event.tracked_instrument_id,
        "calendar_event_id": event.calendar_event_id,
        "company_name": event.company_name,
        "instrument": event.instrument,
        "market": event.market,
        "source": event.source,
        "external_key": event.external_key,
        "kind": event.kind,
        "title": event.title,
        "event_at": iso(event.event_at),
        "event_time_status": event.event_time_status.value,
        "status": event.status.value,
        "resolved_etoro_instrument_id": event.resolved_etoro_instrument_id,
        "resolved_etoro_symbol": event.resolved_etoro_symbol,
        "resolved_etoro_display_name": event.resolved_etoro_display_name,
        "resolution_armed_at": iso(event.resolution_armed_at),
        "resolution_armed_by": event.resolution_armed_by,
        "reference_price": str(event.reference_price) if event.reference_price is not None else None,
        "reference_captured_at": iso(event.reference_captured_at),
        "reference_kind": event.reference_kind,
        "reaction_anchor_at": iso(event.reaction_anchor_at),
        "started_at": iso(event.started_at),
        "completed_at": iso(event.completed_at),
        "last_error": event.last_error,
        "updated_at": iso(event.updated_at),
    }


_POSTGRES_UUID_TEXT = re.compile(
    r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _require_valid_calendar_event_id(calendar_event_id: str) -> str:
    if _POSTGRES_UUID_TEXT.fullmatch(calendar_event_id) is None:
        raise HTTPException(
            status_code=400, detail="calendar_event_id must be a valid UUID"
        )
    return calendar_event_id


class ManualCalendarEventRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    instrument: str = Field(min_length=1, max_length=40)
    market: str = Field(min_length=1, max_length=100)
    event_type: str = Field(min_length=1, max_length=100)
    scheduled_date: date
    source: str = Field(default="manual", min_length=1, max_length=100)
    initial_status: str = Field(default="candidate", pattern="^(candidate|tracked)$")
    occurrence_key: str | None = Field(default=None, min_length=1, max_length=200)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def create_app(
    repository: EventExpectationRepository | None = None,
    *,
    paper_repository: PaperStatusRepository | None = None,
    approval_repository: StrategyDraftApprovalRepository | None = None,
    calendar_repository: CalendarEventRepository | None = None,
    tracked_event_repository: SupabaseTrackedEventRepository | None = None,
    admin_token: str | None = None,
    read_api_key: str | None = None,
    control_api_key: str | None = None,
    market_analyzer=_analyze_market_ticker,
    market_scanner=_scan_market,
    market_symbol_searcher=_search_market_symbols,
) -> FastAPI:
    app = FastAPI(
        title="MarketAI API",
        version="0.1.0",
        description="Backend API for versioned event research and paper trading.",
    )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_json_safe(jsonable_encoder({"detail": exc.errors()})),
        )

    repo_cache: EventExpectationRepository | None = repository
    paper_repo_cache: PaperStatusRepository | None = paper_repository
    approval_repo_cache: StrategyDraftApprovalRepository | None = approval_repository
    calendar_repo_cache: CalendarEventRepository | None = calendar_repository
    tracked_event_repo_cache: SupabaseTrackedEventRepository | None = tracked_event_repository
    configured_admin_token = admin_token or os.environ.get("MARKETAI_ADMIN_API_KEY")
    configured_read_api_key = read_api_key or os.environ.get("MARKETAI_READ_API_KEY")
    configured_control_api_key = control_api_key or os.environ.get("MARKETAI_CONTROL_API_KEY")

    def get_repository() -> EventExpectationRepository:
        nonlocal repo_cache
        if repo_cache is None:
            repo_cache = SupabaseEventExpectationRepository.from_env()
        return repo_cache

    def get_paper_repository() -> PaperStatusRepository:
        nonlocal paper_repo_cache
        if paper_repo_cache is None:
            paper_repo_cache = SupabasePaperTradeRepository.from_env()
        return paper_repo_cache

    def get_approval_repository() -> StrategyDraftApprovalRepository:
        nonlocal approval_repo_cache
        if approval_repo_cache is None:
            approval_repo_cache = SupabaseStrategyDraftApprovalRepository.from_env()
        return approval_repo_cache

    def get_calendar_repository() -> CalendarEventRepository:
        nonlocal calendar_repo_cache
        if calendar_repo_cache is None:
            calendar_repo_cache = SupabaseCalendarEventRepository.from_env()
        return calendar_repo_cache

    def get_tracked_event_repository() -> SupabaseTrackedEventRepository:
        nonlocal tracked_event_repo_cache
        if tracked_event_repo_cache is None:
            tracked_event_repo_cache = SupabaseTrackedEventRepository.from_env()
        return tracked_event_repo_cache

    def require_admin(x_admin_token: str | None) -> None:
        if not configured_admin_token:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Event editing is disabled until MARKETAI_ADMIN_API_KEY is configured")
        if not x_admin_token or not secrets.compare_digest(x_admin_token, configured_admin_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")

    def require_read(x_marketai_key: str | None) -> None:
        if not configured_read_api_key:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Read access is disabled until MARKETAI_READ_API_KEY is configured")
        if not x_marketai_key or not secrets.compare_digest(x_marketai_key, configured_read_api_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing read API key")

    def require_control(x_marketai_control_key: str | None) -> None:
        if not configured_control_api_key:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Strategy approval is disabled until MARKETAI_CONTROL_API_KEY is configured")
        if not x_marketai_control_key or not secrets.compare_digest(x_marketai_control_key, configured_control_api_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing control API key")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "trading_mode": "PAPER"}

    @app.get("/api/v1/events")
    def list_events(x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key")) -> list[dict[str, Any]]:
        require_read(x_marketai_key)
        try:
            return [_expectation_payload(item) for item in get_repository().list_upcoming()]
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/v1/tracked-events")
    def list_tracked_events(
        limit: int = Query(default=20, ge=1, le=100),
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> list[dict[str, Any]]:
        require_read(x_marketai_key)
        try:
            return [
                _tracked_event_payload(item)
                for item in get_tracked_event_repository().list_recent(limit=limit)
            ]
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/v1/symbols")
    def symbols(q: str = Query(..., max_length=100), limit: int = 8, x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key")) -> list[dict[str, str]]:
        require_read(x_marketai_key)
        if len(q.strip()) < 1:
            return []
        return market_symbol_searcher(q, limit)

    @app.get("/api/v1/market-memory/{ticker}")
    def market_memory_analysis(ticker: str, x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key")) -> dict[str, Any]:
        require_read(x_marketai_key)
        try:
            return market_analyzer(ticker)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/scanner")
    def scanner(market: str = "Finland Top", limit: int = 10, x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key")) -> dict[str, Any]:
        require_read(x_marketai_key)
        try:
            return market_scanner(market, limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/v1/events/{event_id}")
    def get_event(event_id: str, x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key")) -> dict[str, Any]:
        require_read(x_marketai_key)
        try:
            expectation = get_repository().get(event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if expectation is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return _expectation_payload(expectation)

    @app.get("/api/v1/events/{event_id}/paper-status")
    def get_paper_status(event_id: str, x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key")) -> dict[str, Any]:
        require_read(x_marketai_key)
        try:
            expectation = get_repository().get(event_id)
            if expectation is None:
                raise HTTPException(status_code=404, detail="Event not found")
            run = get_paper_repository().get_latest_for_event(event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"event_id": event_id, "instrument": expectation.instrument, "event_name": expectation.event_name, "scheduled_date": expectation.scheduled_date, "expectation_version": expectation.version, "paper_run": run, "trading_mode": "PAPER"}

    @app.post("/api/v1/events/{event_id}/expectation-versions", status_code=status.HTTP_201_CREATED)
    def create_expectation_version(event_id: str, request: ExpectationVersionRequest, x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict[str, Any]:
        require_admin(x_admin_token)
        patch = ExpectationPatch(
            consensus=request.consensus,
            important_kpis=tuple(request.important_kpis) if request.important_kpis is not None else None,
            bull_case=tuple(request.bull_case) if request.bull_case is not None else None,
            base_case=tuple(request.base_case) if request.base_case is not None else None,
            bear_case=tuple(request.bear_case) if request.bear_case is not None else None,
            triggers=request.triggers,
            invalidation_conditions=tuple(request.invalidation_conditions) if request.invalidation_conditions is not None else None,
            source_name=request.source_name,
            source_url=request.source_url,
            source_as_of=request.source_as_of,
        )
        try:
            saved = get_repository().apply_partial_update(
                event_id, patch, change_note=request.change_note
            )
        except EventExpectationNotFound as exc:
            raise HTTPException(status_code=404, detail="Event not found") from exc
        return _expectation_payload(saved)

    @app.post("/api/v1/events/{event_id}/strategy-draft/preview")
    def preview_strategy_draft(
        event_id: str,
        request: StrategyDraftPayload,
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> dict[str, Any]:
        require_read(x_marketai_key)
        try:
            current = get_repository().get(event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if current is None:
            raise HTTPException(status_code=404, detail="Event not found")

        normalized = normalize_draft(event_id, request)
        return {
            "event_id": event_id,
            "base_expectation_version": current.version,
            "draft": normalized,
            "draft_fingerprint": draft_fingerprint(normalized),
            "current": _expectation_payload(current),
            "changed_fields": changed_fields(normalized, current),
            "warnings": draft_warnings(normalized, current),
        }

    @app.post(
        "/api/v1/events/{event_id}/strategy-draft/approve",
        status_code=status.HTTP_201_CREATED,
    )
    def approve_strategy_draft(
        event_id: str,
        request: StrategyDraftApprovalRequest,
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
    ) -> dict[str, Any]:
        require_control(x_marketai_control_key)
        repo = get_repository()
        try:
            current = repo.get(event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if current is None:
            raise HTTPException(status_code=404, detail="Event not found")

        normalized = normalize_draft(event_id, request.draft)
        recomputed_fingerprint = draft_fingerprint(normalized)
        if not secrets.compare_digest(recomputed_fingerprint, request.draft_fingerprint):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Draft fingerprint mismatch: the draft changed since it was previewed. Request a new preview before approving.",
            )

        mismatches = identity_mismatches(normalized, current)
        if mismatches:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Draft identity does not match event '{event_id}' from the URL, "
                    f"which is authoritative: {'; '.join(mismatches)}."
                ),
            )

        try:
            result = get_approval_repository().approve(
                event_id=event_id,
                expected_base_version=request.base_expectation_version,
                source_name=normalized["source_name"],
                source_url=normalized["source_url"],
                source_as_of=date.fromisoformat(normalized["source_as_of"])
                if normalized["source_as_of"]
                else None,
                consensus=normalized["consensus"],
                important_kpis=normalized["important_kpis"],
                bull_case=normalized["bull_case"],
                base_case=normalized["base_case"],
                bear_case=normalized["bear_case"],
                triggers=normalized["triggers"],
                invalidation_conditions=normalized["invalidation_conditions"],
                change_note=normalized["change_note"],
                draft_fingerprint=request.draft_fingerprint,
                approved_by=request.approved_by,
                approved_via=request.approved_via or "unspecified",
            )
        except ExpectationVersionConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Expectation version changed since preview "
                    f"(expected {request.base_expectation_version}). Request a new preview before approving. "
                    f"({exc})"
                ),
            ) from exc
        except StrategyDraftEventNotFound as exc:
            raise HTTPException(status_code=404, detail="Event not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        saved = replace(
            current,
            consensus=normalized["consensus"],
            important_kpis=tuple(normalized["important_kpis"]),
            bull_case=tuple(normalized["bull_case"]),
            base_case=tuple(normalized["base_case"]),
            bear_case=tuple(normalized["bear_case"]),
            triggers=normalized["triggers"],
            invalidation_conditions=tuple(normalized["invalidation_conditions"]),
            source_name=normalized["source_name"],
            source_url=normalized["source_url"],
            source_as_of=date.fromisoformat(normalized["source_as_of"])
            if normalized["source_as_of"]
            else None,
            version=result.version,
            updated_at=result.updated_at,
        )

        return {
            **_expectation_payload(saved),
            "draft_fingerprint": request.draft_fingerprint,
            "approved_by": request.approved_by,
        }

    @app.get("/api/v1/calendar/upcoming")
    def list_upcoming_calendar_events(
        from_date: date | None = Query(default=None),
        to_date: date | None = Query(default=None),
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> list[dict[str, Any]]:
        require_read(x_marketai_key)
        range_start = from_date or date.today()
        range_end = to_date or date.fromordinal(range_start.toordinal() + MAX_CALENDAR_LOOKAHEAD_DAYS)
        if (range_end.toordinal() - range_start.toordinal()) > MAX_CALENDAR_LOOKAHEAD_DAYS:
            raise HTTPException(
                status_code=422,
                detail=f"calendar lookahead cannot exceed {MAX_CALENDAR_LOOKAHEAD_DAYS} days",
            )
        try:
            events = get_calendar_repository().list_upcoming(range_start, range_end)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return [_calendar_event_payload(event) for event in events]

    @app.post("/api/v1/calendar/{calendar_event_id}/track")
    def track_calendar_event(
        calendar_event_id: str,
        x_marketai_control_key: str | None = Header(default=None, alias="X-MarketAI-Control-Key"),
    ) -> dict[str, Any]:
        require_control(x_marketai_control_key)
        calendar_event_id = _require_valid_calendar_event_id(calendar_event_id)
        try:
            updated = get_calendar_repository().track(calendar_event_id)
        except CalendarEventNotFound as exc:
            raise HTTPException(status_code=404, detail="Calendar event not found") from exc
        except InvalidCalendarEventTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _calendar_event_payload(updated)

    @app.post("/api/v1/calendar/{calendar_event_id}/untrack")
    def untrack_calendar_event(
        calendar_event_id: str,
        x_marketai_control_key: str | None = Header(default=None, alias="X-MarketAI-Control-Key"),
    ) -> dict[str, Any]:
        require_control(x_marketai_control_key)
        calendar_event_id = _require_valid_calendar_event_id(calendar_event_id)
        try:
            updated = get_calendar_repository().untrack(calendar_event_id)
        except CalendarEventNotFound as exc:
            raise HTTPException(status_code=404, detail="Calendar event not found") from exc
        except InvalidCalendarEventTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _calendar_event_payload(updated)

    @app.post("/api/v1/calendar/manual", status_code=status.HTTP_201_CREATED)
    def add_manual_calendar_event(
        request: ManualCalendarEventRequest,
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        candidate = CalendarCandidate(
            company_name=request.company_name,
            instrument=request.instrument,
            market=request.market,
            event_type=request.event_type,
            scheduled_date=request.scheduled_date,
            source=request.source,
            occurrence_key=request.occurrence_key or "manual",
        )
        try:
            saved = get_calendar_repository().add_manual_event(
                candidate, status=CalendarEventStatus(request.initial_status)
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _calendar_event_payload(saved)

    return app


app = create_app()