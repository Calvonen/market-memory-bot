from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

from trading_system.ai_event_analyzer import EventAnalyzer, build_default_event_analyzer
from trading_system.global_release_discovery import FinnhubOfficialResultsProvider
from trading_system.manual_release_ingestion import ManualOfficialReleaseProvider
from trading_system.models import EventExpectation
from trading_system.official_release_source_repository import (
    SupabaseOfficialReleaseSourceRepository,
)
from trading_system.release_repository import SupabaseReleaseRepository
from trading_system.release_worker import EventReleaseMonitor, IngestionResult
from trading_system.results_page_release_ingestion import (
    ResultsPageOfficialReleaseProvider,
)
from trading_system.sec_release_ingestion import SecEdgarResultsProvider
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository


DEFAULT_LOOKBACK_DAYS = 1
DEFAULT_LOOKAHEAD_DAYS = 0
TARGET_PAGE_SIZE = 1000
US_MARKET_LABELS = ("US", "USA", "NASDAQ", "NYSE", "AMEX")
DATE_ONLY_OVERDUE_GRACE_HOURS = 24.0
RELEASE_ELIGIBLE_TRACKED_STATUSES = ("tracked", "monitoring", "completed", "failed")
ACTION_REQUIRED_PROVIDER = "canonical_release_worker"
RELEASE_SHELL_IDENTITY_CONFLICTS = frozenset(
    {
        "tracked_release_shell_identity_conflict",
        "tracked_release_calendar_binding_identity_conflict",
    }
)
ACTION_REQUIRED_PREFIX = "action_required:"
MISSING_OFFICIAL_SOURCE_BLOCKER = (
    "no approved official release source and no automatic release provider resolved"
)
LEGACY_MISSING_OFFICIAL_SOURCE_BLOCKER = (
    "earnings target outside approved us market labels requires "
    "an approved official release source"
)


@dataclass(frozen=True)
class CalendarReleaseTarget:
    calendar_event_id: str | None
    event_id: str
    ticker: str
    scheduled_date: date
    market: str = ""
    tracked_event_id: str = ""


AutomaticReleaseProviderFactory = Callable[[CalendarReleaseTarget], Any | None]


class SupabaseCalendarReleaseTargetRepository:
    """Enumerate canonical tracked-event release targets across all producers."""

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
        # Do not apply a lower date bound here. An event that failed release,
        # Supabase or AI processing must remain eligible for a later timer run
        # until an analysis exists for its current expectation version. The
        # caller's start_date is retained for API compatibility and observability.
        targets: list[CalendarReleaseTarget] = []
        cursor: str | None = None

        while True:
            query = (
                self.client.table("tracked_market_events")
                .select("id,calendar_event_id,instrument,event_date,market")
                .eq("kind", "earnings")
                .in_("status", RELEASE_ELIGIBLE_TRACKED_STATUSES)
                .lte("event_date", end_date.isoformat())
                .order("id")
                .limit(TARGET_PAGE_SIZE)
            )
            if cursor is not None:
                query = query.gt("id", cursor)
            response = query.execute()
            rows = list(response.data or [])

            for row in rows:
                if not isinstance(row, dict):
                    raise RuntimeError("tracked release target row is not an object")
                tracked_id = str(row.get("id") or "").strip()
                calendar_id_raw = row.get("calendar_event_id")
                calendar_id = str(calendar_id_raw).strip() if calendar_id_raw else None
                ticker = str(row.get("instrument") or "").strip().upper()
                market = str(row.get("market") or "").strip().upper()
                scheduled = row.get("event_date")
                if not tracked_id or not ticker or not market or not scheduled:
                    row_identity = tracked_id or "<missing-id>"
                    raise RuntimeError(
                        f"tracked release target {row_identity} is missing required canonical data"
                    )
                try:
                    parsed_date = (
                        scheduled
                        if isinstance(scheduled, date)
                        else date.fromisoformat(str(scheduled))
                    )
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"tracked release target {tracked_id} has invalid event_date"
                    ) from exc
                event_id = (
                    f"calendar:{calendar_id}"
                    if calendar_id is not None
                    else f"tracked:{tracked_id}"
                )
                targets.append(
                    CalendarReleaseTarget(
                        calendar_event_id=calendar_id,
                        event_id=event_id,
                        ticker=ticker,
                        scheduled_date=parsed_date,
                        market=market,
                        tracked_event_id=tracked_id,
                    )
                )

            if len(rows) < TARGET_PAGE_SIZE:
                break
            next_cursor = str(rows[-1].get("id") or "").strip()
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("tracked release target pagination cursor did not advance")
            cursor = next_cursor

        return tuple(targets)

    def ensure_release_shell(self, target: CalendarReleaseTarget) -> str:
        tracked_id = target.tracked_event_id.strip()
        if not tracked_id:
            raise RuntimeError("tracked release target is missing tracked_event_id")
        response = self.client.rpc(
            "ensure_tracked_event_release_shell_with_blocker",
            {"input_tracked_event_id": tracked_id},
        ).execute()
        rows = list(response.data or [])
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("canonical release-shell RPC returned invalid response")
        blocker_code = str(rows[0].get("out_blocker_code") or "").strip()
        if blocker_code:
            raise RuntimeError(blocker_code)
        release_event_id = str(rows[0].get("out_release_event_id") or "").strip()
        if release_event_id != target.event_id:
            raise RuntimeError("canonical release-shell identity differs from release target")
        return release_event_id


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


def _persist_action_required(
    releases: SupabaseReleaseRepository,
    *,
    event_id: str,
    message: str,
) -> None:
    """Persist a durable blocker so workflow readiness can surface it.

    The readiness loader already treats persisted ingestion errors as
    ``action_required``. Keep the worker's specific result status for logs and
    diagnostics, but record the blocker through the same durable ingestion-run
    channel used by normal release polling instead of leaving it process-local.
    """
    releases.record_run(
        event_id=event_id,
        provider=ACTION_REQUIRED_PROVIDER,
        status="error",
        error_message=f"{ACTION_REQUIRED_PREFIX} {message}"[:500],
    )


def _is_release_shell_identity_conflict(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in RELEASE_SHELL_IDENTITY_CONFLICTS)


def _is_calendar_binding_identity_conflict(exc: Exception) -> bool:
    return "tracked_release_calendar_binding_identity_conflict" in str(exc).lower()


def _canonical_blocker_message(run: dict[str, Any] | None) -> str | None:
    if not isinstance(run, dict):
        return None
    provider = str(run.get("provider") or "").strip().lower()
    status = str(run.get("status") or "").strip().lower()
    message = str(run.get("error_message") or "").strip().lower()
    if provider != ACTION_REQUIRED_PROVIDER or status != "error":
        return None
    if not message.startswith(ACTION_REQUIRED_PREFIX):
        return None
    return message


def _is_release_shell_blocker(run: dict[str, Any] | None) -> bool:
    message = _canonical_blocker_message(run)
    if message is None:
        return False
    return any(
        marker in message
        for marker in (
            "canonical release-shell identity conflicts",
            "current_event_expectations row is missing",
            "release-shell instrument differs",
            "release-shell date differs",
        )
    )


def _is_missing_official_source_blocker(run: dict[str, Any] | None) -> bool:
    message = _canonical_blocker_message(run)
    if message is None:
        return False
    return any(
        marker in message
        for marker in (
            MISSING_OFFICIAL_SOURCE_BLOCKER,
            LEGACY_MISSING_OFFICIAL_SOURCE_BLOCKER,
        )
    )


def _persist_canonical_validation_if_needed(
    releases: SupabaseReleaseRepository,
    *,
    event_id: str,
    current_version_analyzed: bool,
) -> None:
    latest_run = getattr(releases, "latest_run", None)
    if not callable(latest_run):
        return
    run = latest_run(event_id=event_id)
    should_clear = _is_release_shell_blocker(run) or (
        current_version_analyzed and _is_missing_official_source_blocker(run)
    )
    if not should_clear:
        return
    releases.record_run(
        event_id=event_id,
        provider=ACTION_REQUIRED_PROVIDER,
        status="validated",
        error_message=None,
    )


def _default_automatic_release_provider(
    target: CalendarReleaseTarget,
) -> Any | None:
    """Return the best built-in automatic provider for this target.

    SEC remains the authoritative built-in adapter for US markets. Other
    markets use Finnhub only to resolve the company's own website; discovery
    then remains on that HTTPS origin and delegates release selection to the
    existing fail-closed results-page provider.
    """
    normalized_market = target.market.strip().upper()
    if normalized_market in US_MARKET_LABELS:
        return SecEdgarResultsProvider(
            ticker=target.ticker,
            scheduled_date=target.scheduled_date,
        )
    finnhub_api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not finnhub_api_key:
        return None
    return FinnhubOfficialResultsProvider(
        event_id=target.event_id,
        ticker=target.ticker,
        scheduled_date=target.scheduled_date,
        api_key=finnhub_api_key,
    )


def run_calendar_release_ingestion_once(
    *,
    targets: SupabaseCalendarReleaseTargetRepository,
    expectations: SupabaseEventExpectationRepository,
    releases: SupabaseReleaseRepository,
    analyzer: EventAnalyzer,
    official_sources: SupabaseOfficialReleaseSourceRepository | None = None,
    automatic_provider_factory: AutomaticReleaseProviderFactory | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
) -> tuple[CalendarReleaseWorkerResult, ...]:
    today = clock().astimezone(UTC).date()
    start_date = today - timedelta(days=max(0, lookback_days))
    end_date = today + timedelta(days=max(0, lookahead_days))
    automatic_provider_factory = (
        automatic_provider_factory or _default_automatic_release_provider
    )

    results: list[CalendarReleaseWorkerResult] = []
    for target in targets.list_targets(start_date=start_date, end_date=end_date):
        try:
            ensure_shell = getattr(targets, "ensure_release_shell", None)
            if callable(ensure_shell):
                try:
                    ensure_shell(target)
                except Exception as exc:
                    if not _is_release_shell_identity_conflict(exc):
                        raise
                    message = "canonical release-shell identity conflicts with tracked-event identity"
                    if not _is_calendar_binding_identity_conflict(exc):
                        _persist_action_required(
                            releases,
                            event_id=target.event_id,
                            message=message,
                        )
                    results.append(
                        CalendarReleaseWorkerResult(
                            target.event_id,
                            "identity_conflict",
                            message,
                        )
                    )
                    continue

            expectation = expectations.get(target.event_id)
            if expectation is None:
                message = "current_event_expectations row is missing"
                _persist_action_required(
                    releases,
                    event_id=target.event_id,
                    message=message,
                )
                results.append(
                    CalendarReleaseWorkerResult(
                        target.event_id,
                        "missing_release_shell",
                        message,
                    )
                )
                continue
            if expectation.instrument.strip().upper() != target.ticker:
                message = "release-shell instrument differs from tracked-event instrument"
                _persist_action_required(
                    releases,
                    event_id=target.event_id,
                    message=message,
                )
                results.append(
                    CalendarReleaseWorkerResult(
                        target.event_id,
                        "identity_conflict",
                        message,
                    )
                )
                continue
            if expectation.scheduled_date != target.scheduled_date:
                message = "release-shell date differs from tracked-event date"
                _persist_action_required(
                    releases,
                    event_id=target.event_id,
                    message=message,
                )
                results.append(
                    CalendarReleaseWorkerResult(
                        target.event_id,
                        "identity_conflict",
                        message,
                    )
                )
                continue

            current_version_analyzed = releases.has_analysis_for_event_version(
                event_id=target.event_id,
                expectation_version=expectation.version,
            )
            _persist_canonical_validation_if_needed(
                releases,
                event_id=target.event_id,
                current_version_analyzed=current_version_analyzed,
            )

            if current_version_analyzed:
                results.append(
                    CalendarReleaseWorkerResult(target.event_id, "already_analyzed")
                )
                continue

            approved_source = (
                official_sources.get(target.event_id)
                if official_sources is not None
                else None
            )
            if approved_source is not None:
                if approved_source.source_kind == "results_page":
                    provider = ResultsPageOfficialReleaseProvider.for_event(
                        approved_source,
                        scheduled_date=target.scheduled_date,
                    )
                else:
                    provider = ManualOfficialReleaseProvider(approved_source)
            else:
                provider = automatic_provider_factory(target)
                if provider is None:
                    message = MISSING_OFFICIAL_SOURCE_BLOCKER
                    _persist_action_required(
                        releases,
                        event_id=target.event_id,
                        message=message,
                    )
                    results.append(
                        CalendarReleaseWorkerResult(
                            target.event_id,
                            "missing_official_source",
                            message,
                        )
                    )
                    continue
            monitor = EventReleaseMonitor(
                expectation_repository=_PinnedExpectationRepository(
                    event_id=target.event_id,
                    expectation=expectation,
                ),
                release_repository=releases,
                analyzer=analyzer,
                provider=provider,
                overdue_grace_hours=DATE_ONLY_OVERDUE_GRACE_HOURS,
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
        official_sources=SupabaseOfficialReleaseSourceRepository.from_env(),
        lookback_days=lookback_days,
        lookahead_days=lookahead_days,
    )
    for result in results:
        detail = f" ({result.message})" if result.message else ""
        print(f"{result.event_id}: {result.status}{detail}", flush=True)


if __name__ == "__main__":
    main()