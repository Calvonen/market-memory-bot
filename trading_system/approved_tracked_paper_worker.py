from __future__ import annotations

import math
import os
import time
from typing import Any

from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.models import PortfolioState
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_event_paper_orchestration import run_approved_tracked_paper_once
from trading_system.tracked_event_repository import SupabaseTrackedEventRepository
from trading_system.trading_task_repository import SupabaseTradingTaskRepository


DEFAULT_POLL_SECONDS = 30.0
DEFAULT_LEASE_SECONDS = 120
DEFAULT_BATCH_SIZE = 50


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


def _portfolio_from_env() -> PortfolioState:
    """Read the operator-supplied PAPER account baseline fresh for one decision."""
    equity = _required_float("MARKETAI_PAPER_EQUITY")
    cash = _required_float("MARKETAI_PAPER_CASH")
    spread_pct = _required_float("MARKETAI_PAPER_SPREAD_PCT")
    if equity <= 0 or cash < 0 or spread_pct < 0:
        raise RuntimeError("PAPER equity must be positive and cash/spread must be non-negative")
    return PortfolioState(
        equity=equity,
        cash=cash,
        open_positions=_required_nonnegative_int("MARKETAI_PAPER_OPEN_POSITIONS"),
        instrument_exposure_pct=_optional_float("MARKETAI_PAPER_INSTRUMENT_EXPOSURE_PCT", 0.0),
        daily_pnl=_optional_float("MARKETAI_PAPER_DAILY_PNL", 0.0),
        spread_pct=spread_pct,
        # Leave volatility unset so the reviewed post-release path derives it
        # from the same market frame used for levels/confirmation.
        volatility_pct=None,
    )


def _approved_task_rows(
    repository: SupabaseTradingTaskRepository,
    page_size: int,
) -> list[dict[str, Any]]:
    """Return every approved PAPER task without allowing the first page to starve later work."""
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


def _executed_paper_orders(
    repository: SupabasePaperTradeRepository,
    page_size: int,
) -> list[dict[str, Any]]:
    """Read the durable internal PAPER orders that remain open in this PR's broker model."""
    orders: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = (
            repository.client.table("event_paper_trade_runs")
            .select("paper_order")
            .eq("status", "paper_executed")
            .order("updated_at")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = response.data or []
        if not isinstance(page, list):
            raise RuntimeError("PAPER portfolio read returned malformed data")
        for row in page:
            if not isinstance(row, dict) or not isinstance(row.get("paper_order"), dict):
                raise RuntimeError("executed PAPER run is missing a canonical paper_order")
            orders.append(row["paper_order"])
        if len(page) < page_size:
            return orders
        offset += page_size


def _paper_portfolio_for_instrument(
    repository: SupabasePaperTradeRepository,
    *,
    instrument: str,
    page_size: int,
) -> PortfolioState:
    """Build current fail-closed PAPER risk state immediately before one decision.

    Environment values are re-read on every task and act as the operator/account
    baseline. Every durable `paper_executed` order produced by the internal broker
    is then folded into open-position count, available cash and instrument exposure.
    The internal broker in this PR has no close lifecycle, so counting its executed
    orders as still open is the conservative representation until eToro Virtual
    Portfolio becomes the authoritative account source in the next execution PR.
    """
    base = _portfolio_from_env()
    target = instrument.strip().upper()
    if not target:
        raise RuntimeError("approved PAPER task has blank instrument")

    orders = _executed_paper_orders(repository, page_size)
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


def run_forever() -> None:
    """Discover approved canonical PAPER tasks and run the tracked-event orchestrator.

    This worker creates and approves nothing. It is the production entry point
    that turns already-approved canonical execution intent into the fail-closed
    orchestration boundary. Internal PaperBroker remains the default in this PR.
    """
    poll_seconds = _positive_float("MARKETAI_APPROVED_PAPER_POLL_SECONDS", DEFAULT_POLL_SECONDS)
    lease_seconds = _positive_int("MARKETAI_APPROVED_PAPER_LEASE_SECONDS", DEFAULT_LEASE_SECONDS)
    batch_size = _positive_int("MARKETAI_APPROVED_PAPER_BATCH_SIZE", DEFAULT_BATCH_SIZE)

    tracked_events = SupabaseTrackedEventRepository.from_env()
    expectations = SupabaseEventExpectationRepository.from_env()
    releases = SupabaseReleaseRepository.from_env()
    trading_tasks = SupabaseTradingTaskRepository.from_env()
    paper_runs = SupabasePaperTradeRepository.from_env()
    resolver = EtoroInstrumentResolver(EtoroMarketDataProvider.from_env())

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
                    # Risk state is deliberately refreshed immediately before each
                    # task. A preceding successful order therefore changes the next
                    # task's open-position/cash/exposure gates in the same worker pass.
                    portfolio = _paper_portfolio_for_instrument(
                        paper_runs,
                        instrument=instrument,
                        page_size=batch_size,
                    )
                    result = run_approved_tracked_paper_once(
                        tracked_event_id=tracked_event_id,
                        task_id=task_id,
                        tracked_events=tracked_events,
                        expectations=expectations,
                        releases=releases,
                        trading_tasks=trading_tasks,
                        paper_runs=paper_runs,
                        resolver=resolver,
                        portfolio=portfolio,
                        lease_seconds=lease_seconds,
                    )
                    print(
                        f"approved-paper task={task_id} event={tracked_event_id} "
                        f"status={result.status} message={result.message}",
                        flush=True,
                    )
                except Exception as exc:
                    # One malformed/not-ready task must not stop discovery for all
                    # other explicitly approved tasks. Every execution boundary
                    # inside the orchestrator remains fail-closed.
                    print(
                        f"approved-paper retryable failure task={task_id} "
                        f"event={tracked_event_id}: {exc}",
                        flush=True,
                    )
        except Exception as exc:
            print(f"approved-paper discovery failure: {exc}", flush=True)

        time.sleep(poll_seconds)


if __name__ == "__main__":
    run_forever()
