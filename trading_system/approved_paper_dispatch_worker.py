from __future__ import annotations

import time
from uuid import uuid4

from trading_system.approved_paper_etoro_session import read_etoro_session_state
from trading_system.approved_tracked_paper_worker import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_POLL_SECONDS,
    DEFAULT_PORTFOLIO_LEASE_SECONDS,
    _PortfolioLeasePaperRuns,
    _approved_task_rows,
    _assert_no_uncertain_broker_attempts,
    _broker_mode,
    _claim_portfolio_lease,
    _etoro_demo_amount_cap,
    _etoro_demo_broker_for_event,
    _etoro_demo_portfolio,
    _paper_portfolio_for_instrument,
    _positive_float,
    _positive_int,
    _release_portfolio_lease,
)
from trading_system.brokers.etoro_demo import EtoroDemoBroker
from trading_system.brokers.paper import PaperBroker
from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.market_open_paper_orchestration import (
    _recover_completed_attempt,
    run_approved_market_open_paper_once,
)
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.pipeline import PaperTradingPipeline
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_event_paper_bridge import canonical_release_event_id
from trading_system.tracked_event_paper_orchestration import run_approved_tracked_paper_once
from trading_system.tracked_event_repository import SupabaseTrackedEventRepository
from trading_system.trading_session_state import TradingSessionState
from trading_system.trading_task_repository import SupabaseTradingTaskRepository


ETORO_SESSION_TIMEOUT_SECONDS = 10.0
ETORO_SESSION_MAX_AGE_SECONDS = 30.0


class _MarketOpenLeasePaperRuns(_PortfolioLeasePaperRuns):
    """Expose one-shot new-attempt preflight before market-open freshness checks."""

    def preflight_new_attempt(self) -> None:
        preflight = self._preflight
        self._preflight = None
        if preflight is not None:
            preflight()


def _unreconciled_market_open_attempts(
    paper_runs: SupabasePaperTradeRepository,
    *,
    limit: int,
) -> tuple[dict, ...]:
    response = paper_runs.client.rpc(
        "list_unreconciled_completed_market_open_broker_attempts",
        {"input_limit": max(1, limit)},
    ).execute()
    rows = tuple(response.data or [])
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("market-open recovery discovery returned malformed state")
        if not str(row.get("event_id") or "").strip() or not str(row.get("task_id") or "").strip():
            raise RuntimeError("market-open recovery discovery returned blank identity")
    return rows


def _run_for_event_kind(
    *,
    event_kind: str,
    tracked_event_id: str,
    task_id: str,
    tracked_events: SupabaseTrackedEventRepository,
    expectations: SupabaseEventExpectationRepository,
    releases: SupabaseReleaseRepository,
    trading_tasks: SupabaseTradingTaskRepository,
    paper_runs: SupabasePaperTradeRepository,
    resolver: EtoroInstrumentResolver,
    portfolio,
    lease_seconds: int,
    pipeline: PaperTradingPipeline,
    session: TradingSessionState | None = None,
):
    normalized = event_kind.strip().lower()
    common = dict(
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
        pipeline=pipeline,
    )
    if normalized == "earnings":
        return run_approved_tracked_paper_once(**common, session=session)
    if normalized == "market_open":
        return run_approved_market_open_paper_once(**common)
    raise ValueError(f"approved PAPER event kind is not executable: {event_kind}")


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
    market_data = EtoroMarketDataProvider.from_env()
    resolver = EtoroInstrumentResolver(market_data)

    while True:
        try:
            # Completed market-open attempts are discovered independently of the
            # current task state. Recovery happens before approved-task discovery,
            # broker construction, eToro access, portfolio reads, or leases.
            for recovery_row in _unreconciled_market_open_attempts(
                paper_runs,
                limit=batch_size,
            ):
                recovery_event_id = str(recovery_row["event_id"])
                recovery_task_id = str(recovery_row["task_id"])
                recovered = _recover_completed_attempt(
                    paper_runs,
                    source_event_id=recovery_event_id,
                    task_id=recovery_task_id,
                )
                if recovered is None:
                    raise RuntimeError(
                        "completed market-open broker attempt remained unreconciled after recovery"
                    )
                print(
                    f"approved-paper recovery broker={broker_mode} task={recovery_task_id} "
                    f"event={recovery_event_id} status={recovered.get('status')} "
                    f"message={recovered.get('message')}",
                    flush=True,
                )

            rows = _approved_task_rows(trading_tasks, batch_size)
            for row in rows:
                task_id = str(row.get("id") or "").strip()
                tracked_event_id = str(row.get("tracked_event_id") or "").strip()
                instrument = str(row.get("instrument") or "").strip()
                if not task_id or not tracked_event_id or not instrument:
                    raise RuntimeError("approved PAPER task discovery returned blank identity")

                try:
                    event = tracked_events.get(tracked_event_id)
                    if event is None:
                        raise RuntimeError("approved PAPER task tracked event is missing")
                    if event.instrument.strip().upper() != instrument.upper():
                        raise RuntimeError("approved PAPER task instrument differs from tracked event")
                    event_kind = event.kind.strip().lower()
                    if event_kind not in {"earnings", "market_open"}:
                        raise RuntimeError(
                            f"approved PAPER task event kind is unsupported: {event.kind}"
                        )

                    # Normal approved-task handling keeps this idempotent recovery
                    # check too; the independent pass above is what covers cancelled
                    # tasks whose completed broker attempt still needs persistence.
                    if event_kind == "market_open":
                        recovered = _recover_completed_attempt(
                            paper_runs,
                            source_event_id=canonical_release_event_id(event),
                            task_id=task_id,
                        )
                        if recovered is not None:
                            print(
                                f"approved-paper kind={event.kind} broker={broker_mode} "
                                f"task={task_id} event={tracked_event_id} "
                                f"status={recovered.get('status')} "
                                f"message={recovered.get('message')}",
                                flush=True,
                            )
                            continue

                    etoro_broker: EtoroDemoBroker | None = None
                    session: TradingSessionState | None = None
                    if broker_mode == "etoro_demo":
                        etoro_broker = _etoro_demo_broker_for_event(
                            event,
                            amount_cap_usd=float(etoro_amount_cap),
                        )
                        broker = etoro_broker
                        if event_kind == "earnings":
                            session = read_etoro_session_state(
                                market_data,
                                instrument_id=etoro_broker.instrument_id,
                                timeout_seconds=ETORO_SESSION_TIMEOUT_SECONDS,
                                max_age_seconds=ETORO_SESSION_MAX_AGE_SECONDS,
                                allow_extended_hours=False,
                            )
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
                        _assert_no_uncertain_broker_attempts(paper_runs)
                        if etoro_broker is not None:
                            portfolio = _etoro_demo_portfolio(etoro_broker)
                        else:
                            portfolio = _paper_portfolio_for_instrument(
                                paper_runs,
                                instrument=instrument,
                                page_size=batch_size,
                            )

                        runs_type = (
                            _MarketOpenLeasePaperRuns
                            if event_kind == "market_open"
                            else _PortfolioLeasePaperRuns
                        )
                        lease_aware_runs = runs_type(
                            paper_runs,
                            portfolio_token=portfolio_token,
                            portfolio_lease_seconds=portfolio_lease_seconds,
                            preflight=(
                                etoro_broker.verify_demo_access
                                if etoro_broker is not None
                                else None
                            ),
                            etoro_broker=etoro_broker,
                        )
                        result = _run_for_event_kind(
                            event_kind=event.kind,
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
                            session=session,
                        )
                        print(
                            f"approved-paper kind={event.kind} broker={broker_mode} "
                            f"task={task_id} event={tracked_event_id} "
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
