from __future__ import annotations

import os
import secrets
from dataclasses import asdict, replace
from datetime import date
from typing import Any, Protocol


from fastapi import FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from trading_system.event_repository import EventExpectationRepository
from trading_system.models import EventExpectation
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository


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


def create_app(
    repository: EventExpectationRepository | None = None,
    *,
    paper_repository: PaperStatusRepository | None = None,
    admin_token: str | None = None,
    read_api_key: str | None = None,
    market_analyzer=_analyze_market_ticker,
    market_scanner=_scan_market,
    market_symbol_searcher=_search_market_symbols,
) -> FastAPI:
    app = FastAPI(
        title="MarketAI API",
        version="0.1.0",
        description="Backend API for versioned event research and paper trading.",
    )
    repo_cache: EventExpectationRepository | None = repository
    paper_repo_cache: PaperStatusRepository | None = paper_repository
    configured_admin_token = admin_token or os.environ.get("MARKETAI_ADMIN_API_KEY")
    configured_read_api_key = read_api_key or os.environ.get("MARKETAI_READ_API_KEY")

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

    def require_admin(x_admin_token: str | None) -> None:
        if not configured_admin_token:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Event editing is disabled until MARKETAI_ADMIN_API_KEY is configured")
        if not x_admin_token or not secrets.compare_digest(x_admin_token, configured_admin_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")

    def require_read(x_marketai_key: str | None) -> None:
        # A separate, lower-privilege credential for read-only clients (the
        # mobile app): it must never be the admin token, and its absence must
        # fail closed (503) rather than silently leaving these endpoints open.
        if not configured_read_api_key:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Read access is disabled until MARKETAI_READ_API_KEY is configured")
        if not x_marketai_key or not secrets.compare_digest(x_marketai_key, configured_read_api_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing read API key")

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
        repo = get_repository()
        current = repo.get(event_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Event not found")
        updated = replace(
            current,
            consensus=request.consensus if request.consensus is not None else current.consensus,
            important_kpis=tuple(request.important_kpis) if request.important_kpis is not None else current.important_kpis,
            bull_case=tuple(request.bull_case) if request.bull_case is not None else current.bull_case,
            base_case=tuple(request.base_case) if request.base_case is not None else current.base_case,
            bear_case=tuple(request.bear_case) if request.bear_case is not None else current.bear_case,
            triggers=request.triggers if request.triggers is not None else current.triggers,
            invalidation_conditions=tuple(request.invalidation_conditions) if request.invalidation_conditions is not None else current.invalidation_conditions,
            source_name=request.source_name if request.source_name is not None else current.source_name,
            source_url=request.source_url if request.source_url is not None else current.source_url,
            source_as_of=request.source_as_of if request.source_as_of is not None else current.source_as_of,
        )
        saved = repo.save(updated, change_note=request.change_note)
        return _expectation_payload(saved)

    return app


app = create_app()
