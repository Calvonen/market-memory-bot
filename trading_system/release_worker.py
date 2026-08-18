from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from typing import Callable

from trading_system.ai_event_analyzer import (
    EventAnalysisPayload,
    EventAnalyzer,
    build_default_event_analyzer,
)
from trading_system.models import EventExpectation, PortfolioState
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.post_release_paper import PostReleasePaperResult, run_post_release_paper
from trading_system.release_ingestion import HaysResultsCentreProvider, OfficialReleaseProvider
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository


@dataclass(frozen=True)
class IngestionResult:
    status: str
    source_document_id: str | None = None
    analysis_id: str | None = None
    message: str | None = None
    expectation: EventExpectation | None = None
    analysis: EventAnalysisPayload | None = None


class EventReleaseMonitor:
    def __init__(
        self,
        *,
        expectation_repository: SupabaseEventExpectationRepository,
        release_repository: SupabaseReleaseRepository,
        analyzer: EventAnalyzer,
        provider: OfficialReleaseProvider,
    ) -> None:
        self.expectations = expectation_repository
        self.releases = release_repository
        self.analyzer = analyzer
        self.provider = provider

    def run_once(self, event_id: str) -> IngestionResult:
        expectation = self.expectations.get(event_id)
        if expectation is None:
            raise ValueError(f"Unknown event: {event_id}")

        try:
            document = self.provider.discover(event_id)
            if document is None:
                self.releases.record_run(
                    event_id=event_id,
                    provider=self.provider.name,
                    status="no_release",
                )
                return IngestionResult(status="no_release")

            existing = self.releases.find_document(event_id, document.content_sha256)
            stored = existing or self.releases.save_document(document)
            document_id = str(stored["id"])

            analysis = self.analyzer.analyze(expectation, document)
            saved_analysis = self.releases.save_analysis(
                event_id=event_id,
                source_document_id=document_id,
                expectation_version=expectation.version,
                analysis=analysis,
            )
            self.releases.record_run(
                event_id=event_id,
                provider=self.provider.name,
                status="analyzed",
                source_url=document.source_url,
                source_document_id=document_id,
            )
            return IngestionResult(
                status="analyzed",
                source_document_id=document_id,
                analysis_id=str(saved_analysis.get("id")) if saved_analysis.get("id") else None,
                message=f"AI provider={analysis.provider}, model={analysis.model}",
                expectation=expectation,
                analysis=analysis.payload,
            )
        except Exception as exc:
            self.releases.record_run(
                event_id=event_id,
                provider=self.provider.name,
                status="error",
                error_message=str(exc)[:2000],
            )
            raise


def build_hays_monitor() -> EventReleaseMonitor:
    return EventReleaseMonitor(
        expectation_repository=SupabaseEventExpectationRepository.from_env(),
        release_repository=SupabaseReleaseRepository.from_env(),
        analyzer=build_default_event_analyzer(),
        provider=HaysResultsCentreProvider(),
    )


def build_paper_portfolio_from_env() -> PortfolioState:
    """Build the paper-only portfolio/risk context used after a release.

    Spread is an explicit paper assumption because the current Yahoo daily feed
    does not provide a live bid/ask spread.  Volatility is left unset here and is
    filled from the latest ATR percentage by the post-release confirmation path.
    """
    equity = float(os.environ.get("MARKETAI_PAPER_EQUITY", "10000"))
    cash = float(os.environ.get("MARKETAI_PAPER_CASH", str(equity)))
    return PortfolioState(
        equity=equity,
        cash=cash,
        open_positions=int(os.environ.get("MARKETAI_PAPER_OPEN_POSITIONS", "0")),
        instrument_exposure_pct=float(os.environ.get("MARKETAI_PAPER_INSTRUMENT_EXPOSURE_PCT", "0")),
        daily_pnl=float(os.environ.get("MARKETAI_PAPER_DAILY_PNL", "0")),
        spread_pct=float(os.environ.get("MARKETAI_PAPER_SPREAD_PCT", "0.30")),
        volatility_pct=None,
    )


def run_paper_confirmation_loop(
    *,
    event_id: str,
    expectation: EventExpectation,
    analysis: EventAnalysisPayload,
    interval_seconds: int,
    once: bool,
    source_document_id: str | None = None,
    analysis_id: str | None = None,
    persistence: SupabasePaperTradeRepository | None = None,
    runner: Callable[..., PostReleasePaperResult] = run_post_release_paper,
    sleeper: Callable[[float], None] = time.sleep,
) -> PostReleasePaperResult:
    """Wait for market confirmation without re-running the LLM analysis."""
    portfolio = build_paper_portfolio_from_env()
    while True:
        result = runner(
            expectation=expectation,
            analysis=analysis,
            portfolio=portfolio,
        )
        if persistence is not None:
            if analysis_id is None:
                raise RuntimeError("paper persistence requires analysis_id")
            persistence.save_result(
                event_id=event_id,
                expectation_version=expectation.version,
                source_document_id=source_document_id,
                analysis_id=analysis_id,
                result=result,
            )
        print(f"{event_id}: {result.status} ({result.message})", flush=True)
        if once or result.status == "paper_executed":
            return result
        sleeper(max(60, interval_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor an official results release")
    parser.add_argument("--event-id", default="hays-fy2026-results")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.environ.get("MARKETAI_RELEASE_POLL_SECONDS", "300")),
    )
    args = parser.parse_args()

    monitor = build_hays_monitor()
    persistence = SupabasePaperTradeRepository.from_env()
    while True:
        result = monitor.run_once(args.event_id)
        detail = f" ({result.message})" if result.message else ""
        print(f"{args.event_id}: {result.status}{detail}", flush=True)

        if result.status == "analyzed":
            if result.expectation is None or result.analysis is None:
                raise RuntimeError("analyzed result is missing expectation or analysis payload")
            if result.analysis_id is None:
                raise RuntimeError("analyzed result is missing persisted analysis id")
            run_paper_confirmation_loop(
                event_id=args.event_id,
                expectation=result.expectation,
                analysis=result.analysis,
                interval_seconds=args.interval_seconds,
                once=args.once,
                source_document_id=result.source_document_id,
                analysis_id=result.analysis_id,
                persistence=persistence,
            )
            return

        if args.once:
            return
        time.sleep(max(60, args.interval_seconds))


if __name__ == "__main__":
    main()
