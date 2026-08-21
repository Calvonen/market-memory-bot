from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from trading_system.calendar_provider import EarningsCalendarProvider, FinnhubEarningsCalendarProvider
from trading_system.calendar_repository import CalendarEventRepository, CalendarSyncResult
from trading_system.supabase_calendar_repository import SupabaseCalendarEventRepository

DEFAULT_LOOKAHEAD_DAYS = 90


def run_sync(
    *,
    provider: EarningsCalendarProvider,
    repository: CalendarEventRepository,
    from_date: date,
    to_date: date,
) -> CalendarSyncResult:
    """Fetches upcoming candidates from `provider` and idempotently syncs
    them into `repository`. Never touches EventExpectationRepository, the
    release worker, or the PAPER pipeline - candidate/tracked calendar
    events cannot influence trading (see calendar_repository.py)."""
    candidates = provider.fetch_upcoming(from_date, to_date)
    return repository.sync_candidates(candidates, source=provider.name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync upcoming earnings-calendar candidates into the calendar/watchlist store"
    )
    parser.add_argument(
        "--lookahead-days",
        type=int,
        default=int(os.environ.get("MARKETAI_CALENDAR_LOOKAHEAD_DAYS", str(DEFAULT_LOOKAHEAD_DAYS))),
    )
    args = parser.parse_args()

    provider = FinnhubEarningsCalendarProvider.from_env()
    repository = SupabaseCalendarEventRepository.from_env()

    today = date.today()
    result = run_sync(
        provider=provider,
        repository=repository,
        from_date=today,
        to_date=today + timedelta(days=args.lookahead_days),
    )

    print(
        f"calendar sync ({provider.name}): "
        f"inserted={len(result.inserted)} updated={len(result.updated)} "
        f"skipped_locked={len(result.skipped_locked)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
