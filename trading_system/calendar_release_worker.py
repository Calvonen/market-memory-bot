from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

from trading_system.ai_event_analyzer import EventAnalyzer, build_default_event_analyzer
from trading_system.models import EventExpectation
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.release_worker import EventReleaseMonitor, IngestionResult
from trading_system.sec_release_ingestion import SecEdgarResultsProvider
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository


DEFAULT_LOOKBACK_DAYS = 1
DEFAULT_LOOKAHEAD_DAYS = 0
TARGET_PAGE_SIZE = 1000
US_MARKET_LABELS = ("USA", "NASDAQ", "NYSE", "AMEX")


@dataclass(frozen=True)
class CalendarReleaseTarget:
    calendar_event_id: str
    event_id: str
    ticker: str
    scheduled_date: date


class SupabaseCalendarReleaseTargetRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseCalendarReleaseTargetRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def list_targets(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[CalendarReleaseTarget, ...]:
        # Do not apply a lower date bound here. An event that failed SEC,
        # Supabase or AI processing must remain eligible for a later timer run
        # until an analysis exists for its current expectation version. The
        # caller's start_date is retained for API compatibility and observability.
        targets: list[CalendarReleaseTarget] = []
        cursor: str | None = None

        while True:
            query = (
                self.client.table("calendar_events")
                .select("id,instrument,scheduled_date")
                .eq("status", "tracked")
                .in_("market", US_MARKET_LABELS)
                .eq("event_type", "earnings")
                .lte("scheduled_date", end_date.isoformat())
                .order("id")
                .limit(TARGET_PAGE_SIZE)
            )
            if cursor is not None:
                query = query.gt("id", cursor)
            response = query.execute()
            rows = list(response.data or [])

            for row in rows:
                if not isinstance(row, dict):
                    raise RuntimeError("calendar release target row is not an object")
                calendar_id = str(row.get("id") or "").strip()
                ticker = str(row.get("instrument") or "").strip().upper()
                scheduled = row.get("scheduled_date")
                if not calendar_id or not ticker or not scheduled:
                    row_identity = calendar_id or "<missing-id>"
                    raise RuntimeError(
                        f"calendar release target {row_identity} is missing required canonical data"
                    )
                try:
                    parsed_date = (
                        scheduled
                        if isinstance(scheduled, date)
                        else date.fromisoformat(str(scheduled))
                    )
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"calendar release target {calendar_id} has invalid scheduled_date"
                    ) from exc
                targets.append(
                    CalendarReleaseTarget(
                        calendar_event_id=calendar_id,
                        event_id=f"calendar:{calendar_id}",
                        ticker=ticker,
                        scheduled_date=parsed_date,
                    )
                )

            if len(rows) < TARGET_PAGE_SIZE:
                break
            next_cursor = str(rows[-1].get("id") or "").strip()
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("calendar release target pagination cursor did not advance")
            cursor = next_cursor

        return tuple(targets)


class _PinnedExpectationRepository:
    """Expose exactly one already-validated expectation to one monitor run."""

    def __init__(self, *, event_id: str, expectation: EventExpectation) -> None:
        self.event_id = event_id
        self.expectation = expectation

    def get(self, event_id: str) -> EventExpectation | None:
        if event_id != self.event_id:
            return None
        return self.expectation


@dataclass(frozen=True)
class CalendarReleaseWorkerResult:
    event_id: str
    status: str
    message: str | None = None


def run_calendar_release_ingestion_once(
    *,
    targets: SupabaseCalendarReleaseTargetRepository,
    expectations: SupabaseEventExpectationRepository,
    releases: SupabaseReleaseRepository,
    analyzer: EventAnalyzer,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
) -> tuple[CalendarReleaseWorkerResult, ...]:
    today = clock().astimezone(UTC).date()
    start_date = today - timedelta(days=max(0, lookback_days))
    end_date = today + timedelta(days=max(0, lookahead_days))

    results: list[CalendarReleaseWorkerResult] = []
    for target in targets.list_targets(start_date=start_date, end_date=end_date):
        try:
            expectation = expectations.get(target.event_id)
            if expectation is None:
                results.append(
                    CalendarReleaseWorkerResult(
                        target.event_id,
                        "missing_release_shell",
                        "current_event_expectations row is missing",
                    )
                )
                continue
            if expectation.instrument.strip().upper() != target.ticker:
                results.append(
                    CalendarReleaseWorkerResult(
                        target.event_id,
                        "identity_conflict",
                        "release-shell instrument differs from calendar instrument",
                    )
                )
                continue
            if expectation.scheduled_date != target.scheduled_date:
                results.append(
                    CalendarReleaseWorkerResult(
                        target.event_id,
                        "identity_conflict",
                        "release-shell date differs from calendar date",
                    )
                )
                continue

            if releases.has_analysis_for_event_version(
                event_id=target.event_id,
                expectation_version=expectation.version,
            ):
                results.append(
                    CalendarReleaseWorkerResult(target.event_id, "already_analyzed")
                )
                continue

            provider = SecEdgarResultsProvider(
                ticker=target.ticker,
                scheduled_date=target.scheduled_date,
            )
            monitor = EventReleaseMonitor(
                expectation_repository=_PinnedExpectationRepository(
                    event_id=target.event_id,
                    expectation=expectation,
                ),
                release_repository=releases,
                analyzer=analyzer,
                provider=provider,
                overdue_grace_hours=float(
                    os.environ.get("MARKETAI_RELEASE_OVERDUE_GRACE_HOURS", "8")
                ),
                clock=clock,
            )
            ingestion: IngestionResult = monitor.run_once(target.event_id)
        except Exception as exc:
            results.append(
                CalendarReleaseWorkerResult(
                    target.event_id,
                    "error",
                    str(exc)[:500],
                )
            )
            continue

        results.append(
            CalendarReleaseWorkerResult(
                target.event_id,
                ingestion.status,
                ingestion.message,
            )
        )
    return tuple(results)


def main() -> None:
    sec_user_agent = os.environ.get("MARKETAI_SEC_USER_AGENT", "").strip()
    if not sec_user_agent:
        raise RuntimeError("MARKETAI_SEC_USER_AGENT is required for SEC release ingestion")
    if "@" not in sec_user_agent:
        raise RuntimeError("MARKETAI_SEC_USER_AGENT must include a contact email address")

    lookback_days = int(
        os.environ.get(
            "MARKETAI_CALENDAR_RELEASE_LOOKBACK_DAYS",
            str(DEFAULT_LOOKBACK_DAYS),
        )
    )
    lookahead_days = int(
        os.environ.get(
            "MARKETAI_CALENDAR_RELEASE_LOOKAHEAD_DAYS",
            str(DEFAULT_LOOKAHEAD_DAYS),
        )
    )
    results = run_calendar_release_ingestion_once(
        targets=SupabaseCalendarReleaseTargetRepository.from_env(),
        expectations=SupabaseEventExpectationRepository.from_env(),
        releases=SupabaseReleaseRepository.from_env(),
        analyzer=build_default_event_analyzer(),
        lookback_days=lookback_days,
        lookahead_days=lookahead_days,
    )
    for result in results:
        detail = f" ({result.message})" if result.message else ""
        print(f"{result.event_id}: {result.status}{detail}", flush=True)


if __name__ == "__main__":
    main()
