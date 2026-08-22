from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from trading_system.calendar_provider import EarningsCalendarProvider, FinnhubEarningsCalendarProvider
from trading_system.calendar_repository import CalendarEventRepository, CalendarSyncResult
from trading_system.supabase_calendar_repository import SupabaseCalendarEventRepository

# Calendar range MVP: the mobile UI only ever offers 7/30-day chips and
# GET /api/v1/calendar/upcoming rejects any lookahead beyond this (see
# MAX_CALENDAR_LOOKAHEAD_DAYS in trading_system/api.py and
# docs/calendar_watchlist.md) - the worker must never fetch, and therefore
# never store, more than that either.
MAX_LOOKAHEAD_DAYS = 30
DEFAULT_LOOKAHEAD_DAYS = 30


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

    # Hard cap, not just a default - a misconfigured MARKETAI_CALENDAR_
    # LOOKAHEAD_DAYS or --lookahead-days must never let this worker fetch
    # (and store) further out than the API itself will ever serve.
    lookahead_days = args.lookahead_days
    if lookahead_days > MAX_LOOKAHEAD_DAYS:
        print(
            f"--lookahead-days={lookahead_days} exceeds the {MAX_LOOKAHEAD_DAYS}-day cap; "
            f"clamping to {MAX_LOOKAHEAD_DAYS}.",
            flush=True,
        )
        lookahead_days = MAX_LOOKAHEAD_DAYS

    provider = FinnhubEarningsCalendarProvider.from_env()
    repository = SupabaseCalendarEventRepository.from_env()

    today = date.today()
    result = run_sync(
        provider=provider,
        repository=repository,
        from_date=today,
        to_date=today + timedelta(days=lookahead_days),
    )

    print(
        f"calendar sync ({provider.name}): "
        f"inserted={len(result.inserted)} updated={len(result.updated)} "
        f"skipped_locked={len(result.skipped_locked)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
