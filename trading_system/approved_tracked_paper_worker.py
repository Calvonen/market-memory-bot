from __future__ import annotations

import math
import os
import time
from typing import Any
from uuid import uuid4

from trading_system.brokers.etoro_demo import EtoroDemoBroker
from trading_system.brokers.paper import PaperBroker
from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.models import PortfolioState
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.pipeline import PaperTradingPipeline
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_event_paper_orchestration import run_approved_tracked_paper_once
from trading_system.tracked_event_repository import PersistentTrackedEvent, SupabaseTrackedEventRepository
from trading_system.trading_task_repository import SupabaseTradingTaskRepository


DEFAULT_POLL_SECONDS = 30.0
DEFAULT_LEASE_SECONDS = 120
DEFAULT_BATCH_SIZE = 50
DEFAULT_PORTFOLIO_LEASE_SECONDS = 900
DEFAULT_BROKER_MODE = "internal"


def _required_float(name: str) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        raise RuntimeError(f"{name} is required for approved PAPER execution")
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if not math.isfinite(value):
        raise RuntimeError(f"{name} must be finite")
    return value


def _optional_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if not math.isfinite(value):
        raise RuntimeError(f"{name} must be finite")
    return value


def _required_nonnegative_int(name: str) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        raise RuntimeError(f"{name} is required for approved PAPER execution")
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 0:
        raise RuntimeError(f"{name} must be non-negative")
    return value


def _positive_float(name: str, default: float) -> float:
    value = _optional_float(name, default)
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be positive")
    return value


def _broker_mode() -> str:
    mode = os.environ.get("MARKETAI_PAPER_BROKER", DEFAULT_BROKER_MODE).strip().lower()
    if mode not in {"internal", "etoro_demo"}:
        raise RuntimeError("MARKETAI_PAPER_BROKER must be 'internal' or 'etoro_demo'")
    return mode


def _etoro_demo_amount_cap() -> float:
    value = _required_float("MARKETAI_ETORO_DEMO_MAX_AMOUNT_USD")
    if value <= 0:
        raise RuntimeError("MARKETAI_ETORO_DEMO_MAX_AMOUNT_USD must be positive")
    return value


def _portfolio_from_env() -> PortfolioState:
    equity = _required_float("MARKETAI_PAPER_EQUITY")
    cash = _required_float("MARKETAI_PAPER_CASH")
    spread_pct = _required_float("MARKETAI_PAPER_SPREAD_PCT")
    exposure_pct = _optional_float("MARKETAI_PAPER_INSTRUMENT_EXPOSURE_PCT", 0.0)
    if equity <= 0 or cash < 0 or spread_pct < 0 or exposure_pct < 0:
        raise RuntimeError(
            "PAPER equity must be positive and cash/spread/instrument exposure must be non-negative"
        )
    return PortfolioState(
        equity=equity,
        cash=cash,
        open_positions=_required_nonnegative_int("MARKETAI_PAPER_OPEN_POSITIONS"),
        instrument_exposure_pct=exposure_pct,
        daily_pnl=_optional_float("MARKETAI_PAPER_DAILY_PNL", 0.0),
        spread_pct=spread_pct,
        volatility_pct=None,
    )


def _approved_task_rows(
    repository: SupabaseTradingTaskRepository,
    page_size: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = (
            repository.client.table("trading_tasks")
            .select("id,tracked_event_id,instrument")
            .eq("state", "approved")
            .eq("mode", "PAPER")
            .order("approved_at")
            .order("id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = response.data or []
        if not isinstance(page, list):
            raise RuntimeError("approved PAPER task discovery returned malformed data")
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def _assert_no_uncertain_broker_attempts(repository: SupabasePaperTradeRepository) -> None:
    response = (
        repository.client.table("event_paper_broker_attempts")
        .select("task_id,event_id,started_at")
        .eq("status", "started")
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if not isinstance(rows, list):
        raise RuntimeError("PAPER uncertain-attempt read returned malformed data")
    if rows:
        raise RuntimeError(
            "PAPER portfolio execution blocked by unresolved broker attempt with uncertain outcome"
        )


def _authoritative_paper_orders(
    repository: SupabasePaperTradeRepository,
    page_size: int,
) -> list[dict[str, Any]]:
    """Return each durable PAPER order exactly once, including unreconciled completions."""
    _assert_no_uncertain_broker_attempts(repository)

    orders_by_task: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        response = (
            repository.client.table("event_paper_trade_runs")
            .select("id,task_id,paper_order")
            .eq("status", "paper_executed")
            .order("updated_at")
            .order("id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = response.data or []
        if not isinstance(page, list):
            raise RuntimeError("PAPER portfolio read returned malformed data")
        for row in page:
            task_id = str(row.get("task_id") or "").strip()
            order = row.get("paper_order")
            if not task_id or not isinstance(order, dict):
                raise RuntimeError("executed PAPER run is missing canonical task/order identity")
            orders_by_task[task_id] = order
        if len(page) < page_size:
            break
        offset += page_size

    offset = 0
    while True:
        response = (
            repository.client.table("event_paper_broker_attempts")
            .select("task_id,order_payload,completed_at")
            .eq("status", "completed")
            .order("completed_at")
            .order("task_id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = response.data or []
        if not isinstance(page, list):
            raise RuntimeError("PAPER broker-attempt portfolio read returned malformed data")
        for row in page:
            task_id = str(row.get("task_id") or "").strip()
            order = row.get("order_payload")
            if not task_id or not isinstance(order, dict):
                raise RuntimeError("completed PAPER broker attempt is missing canonical order identity")
            existing = orders_by_task.get(task_id)
            if existing is not None and existing != order:
                raise RuntimeError("PAPER run and completed broker attempt disagree on order payload")
            orders_by_task[task_id] = order
        if len(page) < page_size:
            break
        offset += page_size

    return list(orders_by_task.values())


def _paper_portfolio_for_instrument(
    repository: SupabasePaperTradeRepository,
    *,
    instrument: str,
    page_size: int,
) -> PortfolioState:
    base = _portfolio_from_env()
    target = instrument.strip().upper()
    if not target:
        raise RuntimeError("approved PAPER task has blank instrument")

    orders = _authoritative_paper_orders(repository, page_size)
    total_notional = 0.0
    instrument_notional = 0.0
    for order in orders:
        order_instrument = str(order.get("instrument") or "").strip().upper()
        try:
            quantity = int(order.get("quantity"))
            reference_price = float(order.get("reference_price"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("executed PAPER order contains malformed sizing") from exc
        if not order_instrument or quantity < 1 or not math.isfinite(reference_price) or reference_price <= 0:
            raise RuntimeError("executed PAPER order contains invalid sizing identity")
        notional = quantity * reference_price
        total_notional += notional
        if order_instrument == target:
            instrument_notional += notional

    persisted_exposure_pct = (instrument_notional / base.equity) * 100.0
    return PortfolioState(
        equity=base.equity,
        cash=max(0.0, base.cash - total_notional),
        open_positions=base.open_positions + len(orders),
        instrument_exposure_pct=base.instrument_exposure_pct + persisted_exposure_pct,
        daily_pnl=base.daily_pnl,
        spread_pct=base.spread_pct,
        volatility_pct=None,
        last_loss_at=base.last_loss_at,
    )


def _claim_portfolio_lease(
    repository: SupabasePaperTradeRepository,
    *,
    token: str,
    lease_seconds: int,
) -> bool:
    response = repository.client.rpc(
        "claim_paper_portfolio_execution_lease",
        {"input_lease_token": token, "input_lease_seconds": lease_seconds},
    ).execute()
    return response.data is True


def _release_portfolio_lease(repository: SupabasePaperTradeRepository, *, token: str) -> None:
    repository.client.rpc(
        "release_paper_portfolio_execution_lease",
        {"input_lease_token": token},
    ).execute()


def _etoro_demo_broker_for_event(
    event: PersistentTrackedEvent,
    *,
    amount_cap_usd: float,
) -> EtoroDemoBroker:
    instrument_id = event.resolved_etoro_instrument_id
    resolved_symbol = str(event.resolved_etoro_symbol or "").strip().upper()
    event_symbol = event.instrument.strip().upper()
    if instrument_id is None or instrument_id <= 0:
        raise RuntimeError("tracked event has no persisted eToro instrument id")
    if not resolved_symbol or resolved_symbol != event_symbol:
        raise RuntimeError("tracked event persisted eToro symbol differs from canonical instrument")
    return EtoroDemoBroker.from_env(
        instrument_id=instrument_id,
        amount_usd=amount_cap_usd,
    )


class _PortfolioLeasePaperRuns:
    """Atomically renew account authority and reserve the broker attempt."""

    def __init__(
        self,
        repository: SupabasePaperTradeRepository,
        *,
        portfolio_token: str,
        portfolio_lease_seconds: int,
    ) -> None:
        self._repository = repository
        self._portfolio_token = portfolio_token
        self._portfolio_lease_seconds = portfolio_lease_seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    def begin_broker_attempt(
        self,
        *,
        event_id: str,
        analysis_id: str,
        task_id: str,
        expectation_version: int,
        claim_token: str,
        execution_token: str,
        lease_seconds: int,
        strategy_payload: dict[str, Any],
        risk_payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._repository.client.rpc(
            "begin_event_paper_broker_attempt_with_portfolio_lease",
            {
                "input_event_id": event_id,
                "input_analysis_id": analysis_id,
                "input_task_id": task_id,
                "input_expectation_version": expectation_version,
                "input_claim_token": claim_token,
                "input_execution_token": execution_token,
                "input_lease_seconds": max(1, lease_seconds),
                "input_strategy_payload": strategy_payload,
                "input_risk_payload": risk_payload,
                "input_portfolio_lease_token": self._portfolio_token,
                "input_portfolio_lease_seconds": self._portfolio_lease_seconds,
            },
        ).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("atomic PAPER portfolio/broker attempt returned no state")
        return rows[0]


def run_forever() -> None:
    poll_seconds = _positive_float("MARKETAI_APPROVED_PAPER_POLL_SECONDS", DEFAULT_POLL_SECONDS)
    lease_seconds = _positive_int("MARKETAI_APPROVED_PAPER_LEASE_SECONDS", DEFAULT_LEASE_SECONDS)
    batch_size = _positive_int("MARKETAI_APPROVED_PAPER_BATCH_SIZE", DEFAULT_BATCH_SIZE)
    portfolio_lease_seconds = _positive_int(
        "MARKETAI_PAPER_PORTFOLIO_LEASE_SECONDS", DEFAULT_PORTFOLIO_LEASE_SECONDS
    )
    broker_mode = _broker_mode()
    etoro_amount_cap = _etoro_demo_amount_cap() if broker_mode == "etoro_demo" else None

    tracked_events = SupabaseTrackedEventRepository.from_env()
    expectations = SupabaseEventExpectationRepository.from_env()
    releases = SupabaseReleaseRepository.from_env()
    trading_tasks = SupabaseTradingTaskRepository.from_env()
    paper_runs = SupabasePaperTradeRepository.from_env()
    resolver = EtoroInstrumentResolver(EtoroMarketDataProvider.from_env())
    demo_access_verified = False

    while True:
        try:
            rows = _approved_task_rows(trading_tasks, batch_size)
            for row in rows:
                task_id = str(row.get("id") or "").strip()
                tracked_event_id = str(row.get("tracked_event_id") or "").strip()
                instrument = str(row.get("instrument") or "").strip()
                if not task_id or not tracked_event_id or not instrument:
                    raise RuntimeError("approved PAPER task discovery returned blank identity")

                try:
                    if broker_mode == "etoro_demo":
                        event = tracked_events.get(tracked_event_id)
                        if event is None:
                            raise RuntimeError("approved PAPER task tracked event is missing")
                        if event.instrument.strip().upper() != instrument.upper():
                            raise RuntimeError("approved PAPER task instrument differs from tracked event")
                        broker = _etoro_demo_broker_for_event(
                            event,
                            amount_cap_usd=float(etoro_amount_cap),
                        )
                        if not demo_access_verified:
                            # Demo account access is checked before any durable broker-attempt
                            # reservation so an auth/configuration failure cannot strand an
                            # uncertain execution attempt.
                            broker.verify_demo_access()
                            demo_access_verified = True
                    else:
                        broker = PaperBroker()

                    portfolio_token = str(uuid4())
                    if not _claim_portfolio_lease(
                        paper_runs,
                        token=portfolio_token,
                        lease_seconds=portfolio_lease_seconds,
                    ):
                        continue
                    try:
                        portfolio = _paper_portfolio_for_instrument(
                            paper_runs,
                            instrument=instrument,
                            page_size=batch_size,
                        )
                        lease_aware_runs = _PortfolioLeasePaperRuns(
                            paper_runs,
                            portfolio_token=portfolio_token,
                            portfolio_lease_seconds=portfolio_lease_seconds,
                        )
                        result = run_approved_tracked_paper_once(
                            tracked_event_id=tracked_event_id,
                            task_id=task_id,
                            tracked_events=tracked_events,
                            expectations=expectations,
                            releases=releases,
                            trading_tasks=trading_tasks,
                            paper_runs=lease_aware_runs,
                            resolver=resolver,
                            portfolio=portfolio,
                            lease_seconds=lease_seconds,
                            pipeline=PaperTradingPipeline(broker=broker),
                        )
                        print(
                            f"approved-paper broker={broker_mode} task={task_id} event={tracked_event_id} "
                            f"status={result.status} message={result.message}",
                            flush=True,
                        )
                    finally:
                        _release_portfolio_lease(paper_runs, token=portfolio_token)
                except Exception as exc:
                    print(
                        f"approved-paper retryable failure broker={broker_mode} task={task_id} "
                        f"event={tracked_event_id}: {exc}",
                        flush=True,
                    )
        except Exception as exc:
            print(f"approved-paper discovery failure: {exc}", flush=True)

        time.sleep(poll_seconds)


if __name__ == "__main__":
    run_forever()
