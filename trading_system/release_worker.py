from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from trading_system.ai_event_analyzer import (
    AIEventAnalysis,
    EventAnalysisPayload,
    EventAnalyzer,
    analysis_from_record,
    build_default_event_analyzer,
)
from trading_system.models import EventExpectation, PortfolioState
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.post_release_paper import (
    PostReleasePaperResult,
    run_post_release_paper,
)
from trading_system.release_ingestion import (
    HaysResultsCentreProvider,
    OfficialReleaseProvider,
)
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository

DEFAULT_OVERDUE_GRACE_HOURS = 8.0
DEFAULT_CONFIRMATION_GRACE_MINUTES = 15
HAYS_LSE_CLOSE_TIME = (16, 30)


@dataclass(frozen=True)
class IngestionResult:
    status: str
    source_document_id: str | None = None
    analysis_id: str | None = None
    message: str | None = None
    expectation: EventExpectation | None = None
    analysis: EventAnalysisPayload | None = None
    overdue: bool = False


def _is_release_overdue(
    expectation: EventExpectation, now: datetime, grace_hours: float
) -> bool:
    """A release is "overdue" once its scheduled date plus a grace window has passed.

    This never invents or assumes a release exists - it only changes how a
    continuing no_release state is logged/audited, so operators watching the
    worker on the actual release day are not left staring at an
    indistinguishable stream of "no_release" lines with no signal that
    something may be wrong (wrong URL, site layout change, missed release).
    """
    scheduled_start = datetime.combine(
        expectation.scheduled_date, datetime.min.time(), tzinfo=UTC
    )
    return now >= scheduled_start + timedelta(hours=grace_hours)


class EventReleaseMonitor:
    def __init__(
        self,
        *,
        expectation_repository: SupabaseEventExpectationRepository,
        release_repository: SupabaseReleaseRepository,
        analyzer: EventAnalyzer,
        provider: OfficialReleaseProvider,
        overdue_grace_hours: float = DEFAULT_OVERDUE_GRACE_HOURS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.expectations = expectation_repository
        self.releases = release_repository
        self.analyzer = analyzer
        self.provider = provider
        self.overdue_grace_hours = overdue_grace_hours
        self.clock = clock

    def run_once(self, event_id: str) -> IngestionResult:
        expectation = self.expectations.get(event_id)
        if expectation is None:
            raise ValueError(f"Unknown event: {event_id}")

        try:
            document = self.provider.discover(event_id)
            if document is None:
                now = self.clock()
                overdue = _is_release_overdue(
                    expectation, now, self.overdue_grace_hours
                )
                note = None
                if overdue:
                    note = (
                        f"release overdue: scheduled_date={expectation.scheduled_date.isoformat()} "
                        f"still no_release as of {now.isoformat()} (grace={self.overdue_grace_hours}h). "
                        "Not inventing a release; verify the source manually or use the release-url override."
                    )
                self.releases.record_run(
                    event_id=event_id,
                    provider=self.provider.name,
                    status="no_release",
                    error_message=note,
                )
                return IngestionResult(
                    status="no_release", overdue=overdue, message=note
                )

            existing = self.releases.find_document(event_id, document.content_sha256)
            stored = existing or self.releases.save_document(document)
            document_id = str(stored["id"])

            # A restart must never re-run the LLM for a document + expectation
            # version that is already persisted, regardless of which provider
            # (Groq vs. Ollama fallback) happened to succeed on a prior attempt.
            existing_analysis = self.releases.find_analysis(
                event_id=event_id,
                source_document_id=document_id,
                expectation_version=expectation.version,
            )
            reused_analysis = existing_analysis is not None
            analysis: AIEventAnalysis
            if existing_analysis is not None:
                analysis = analysis_from_record(existing_analysis)
                saved_analysis = existing_analysis
            else:
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
            message = f"AI provider={analysis.provider}, model={analysis.model}"
            if reused_analysis:
                message += " (reused persisted analysis, no AI call)"
            return IngestionResult(
                status="analyzed",
                source_document_id=document_id,
                analysis_id=(
                    str(saved_analysis.get("id")) if saved_analysis.get("id") else None
                ),
                message=message,
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


def build_hays_monitor(
    *, release_url_override: str | None = None
) -> EventReleaseMonitor:
    """Build the Hays FY2026 monitor.

    `release_url_override` is a plain, explicit URL - never a secret - read
    from `MARKETAI_HAYS_RELEASE_URL` or the `--release-url` CLI flag. When
    set, discovery trusts that exact URL instead of scanning/matching the
    results-centre listing page, for use if the site's title/link format
    ever drifts away from what automatic matching expects.
    """
    return EventReleaseMonitor(
        expectation_repository=SupabaseEventExpectationRepository.from_env(),
        release_repository=SupabaseReleaseRepository.from_env(),
        analyzer=build_default_event_analyzer(),
        provider=HaysResultsCentreProvider(override_url=release_url_override),
        overdue_grace_hours=float(
            os.environ.get(
                "MARKETAI_RELEASE_OVERDUE_GRACE_HOURS", str(DEFAULT_OVERDUE_GRACE_HOURS)
            )
        ),
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
        instrument_exposure_pct=float(
            os.environ.get("MARKETAI_PAPER_INSTRUMENT_EXPOSURE_PCT", "0")
        ),
        daily_pnl=float(os.environ.get("MARKETAI_PAPER_DAILY_PNL", "0")),
        spread_pct=float(os.environ.get("MARKETAI_PAPER_SPREAD_PCT", "0.30")),
        volatility_pct=None,
    )


def _terminal_paper_status(
    persistence: SupabasePaperTradeRepository | None, event_id: str
) -> str | None:
    if persistence is None:
        return None
    latest = persistence.get_latest_for_event(event_id)
    if latest is None or latest.get("status") not in {
        "paper_executed",
        "expired_no_trade",
    }:
        return None
    return str(latest["status"])


def hays_confirmation_deadline(
    expectation: EventExpectation,
    *,
    grace_minutes: int = DEFAULT_CONFIRMATION_GRACE_MINUTES,
) -> datetime:
    """Temporary event/session policy: LSE close on release day plus a small grace.

    This intentionally covers Hays/LSE only; it is not a general exchange calendar.
    Europe/London provides deterministic DST conversion and the persisted result is UTC.
    """
    close_hour, close_minute = HAYS_LSE_CLOSE_TIME
    local_close = datetime.combine(
        expectation.scheduled_date,
        datetime.min.time().replace(hour=close_hour, minute=close_minute),
        tzinfo=ZoneInfo("Europe/London"),
    )
    return (local_close + timedelta(minutes=max(0, grace_minutes))).astimezone(UTC)


def _persisted_deadline(row: dict[str, object] | None) -> datetime | None:
    value = row.get("confirmation_deadline_at") if row else None
    if not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)


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
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    confirmation_grace_minutes: int | None = None,
) -> PostReleasePaperResult:
    """Wait for market confirmation without re-running the LLM analysis.

    Fail-closed also covers restarts: if a paper order was already recorded
    for this event, the loop exits immediately without calling the runner
    (Strategy -> Risk -> PaperBroker) again, so a crash/restart can never
    create a second simulated order for the same event.
    """
    latest = (
        persistence.get_latest_for_event(event_id) if persistence is not None else None
    )
    if latest is not None and latest.get("status") in {
        "paper_executed",
        "expired_no_trade",
    }:
        terminal_status = str(latest["status"])
        message = (
            f"terminal paper state already recorded for {event_id}; skipping re-run"
        )
        print(f"{event_id}: {terminal_status} ({message})", flush=True)
        return PostReleasePaperResult(terminal_status, message)

    grace = confirmation_grace_minutes
    if grace is None:
        grace = int(
            os.environ.get(
                "MARKETAI_CONFIRMATION_GRACE_MINUTES",
                str(DEFAULT_CONFIRMATION_GRACE_MINUTES),
            )
        )
    deadline = _persisted_deadline(latest) or hays_confirmation_deadline(
        expectation, grace_minutes=grace
    )

    portfolio = build_paper_portfolio_from_env()
    while True:
        now = clock().astimezone(UTC)
        if now >= deadline:
            expired = PostReleasePaperResult(
                "expired_no_trade",
                "confirmation window expired without a trade",
                confirmation_deadline_at=deadline,
                expired_at=now,
            )
            if persistence is not None:
                if analysis_id is None:
                    raise RuntimeError("paper persistence requires analysis_id")
                updated = persistence.expire_waiting(
                    event_id=event_id,
                    expectation_version=expectation.version,
                    source_document_id=source_document_id,
                    analysis_id=analysis_id,
                    confirmation_deadline_at=deadline,
                    expired_at=now,
                )
                if updated is not None and updated.get("status") == "paper_executed":
                    return PostReleasePaperResult(
                        "paper_executed",
                        str(updated.get("message") or "paper trade executed"),
                    )
            print(f"{event_id}: {expired.status} ({expired.message})", flush=True)
            return expired

        result = runner(
            expectation=expectation,
            analysis=analysis,
            portfolio=portfolio,
        )
        result = replace(result, confirmation_deadline_at=deadline)
        if persistence is not None:
            if analysis_id is None:
                raise RuntimeError("paper persistence requires analysis_id")
            persisted = persistence.save_result(
                event_id=event_id,
                expectation_version=expectation.version,
                source_document_id=source_document_id,
                analysis_id=analysis_id,
                result=result,
            )
            if persisted.get("status") == "expired_no_trade":
                result = PostReleasePaperResult(
                    "expired_no_trade",
                    str(
                        persisted.get("message")
                        or "confirmation window expired without a trade"
                    ),
                    completed_components=result.completed_components,
                    confirmation_deadline_at=deadline,
                    expired_at=(
                        datetime.fromisoformat(
                            str(persisted["expired_at"]).replace("Z", "+00:00")
                        )
                        if persisted.get("expired_at")
                        else clock().astimezone(UTC)
                    ),
                )
            elif persisted.get("status") == "paper_executed" and result.status != "paper_executed":
                result = PostReleasePaperResult(
                    "paper_executed",
                    str(persisted.get("message") or "paper trade already executed"),
                    confirmation_deadline_at=deadline,
                )
        print(f"{event_id}: {result.status} ({result.message})", flush=True)
        if once or result.status in {"paper_executed", "expired_no_trade"}:
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
    parser.add_argument(
        "--release-url",
        default=os.environ.get("MARKETAI_HAYS_RELEASE_URL") or None,
        help=(
            "Explicit official Hays release URL (HTML or PDF) to use instead of "
            "results-centre auto-discovery. Also settable via MARKETAI_HAYS_RELEASE_URL."
        ),
    )
    args = parser.parse_args()

    monitor = build_hays_monitor(release_url_override=args.release_url)
    persistence = SupabasePaperTradeRepository.from_env()

    # Fail closed on restart for either terminal state before touching the
    # provider, analyzer, or Strategy/Risk/PaperBroker pipeline.
    terminal_status = _terminal_paper_status(persistence, args.event_id)
    if terminal_status is not None:
        print(
            f"{args.event_id}: {terminal_status} already recorded; "
            "exiting without re-running the pipeline.",
            flush=True,
        )
        return

    while True:
        result = monitor.run_once(args.event_id)
        detail = f" ({result.message})" if result.message else ""
        overdue_tag = " [RELEASE OVERDUE]" if result.overdue else ""
        print(f"{args.event_id}: {result.status}{overdue_tag}{detail}", flush=True)

        if result.status == "analyzed":
            if result.expectation is None or result.analysis is None:
                raise RuntimeError(
                    "analyzed result is missing expectation or analysis payload"
                )
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
                confirmation_grace_minutes=int(
                    os.environ.get(
                        "MARKETAI_CONFIRMATION_GRACE_MINUTES",
                        str(DEFAULT_CONFIRMATION_GRACE_MINUTES),
                    )
                ),
            )
            return

        if args.once:
            return
        time.sleep(max(60, args.interval_seconds))


if __name__ == "__main__":
    main()
