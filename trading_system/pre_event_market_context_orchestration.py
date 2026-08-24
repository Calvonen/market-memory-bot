from __future__ import annotations

from datetime import date

from market_memory.data import fetch_ohlcv
from trading_system.pre_event_market_context_acquisition import (
    DailyOhlcvFetcher,
    acquire_pre_event_market_context,
)
from trading_system.pre_event_market_context_persistence import capture_pre_event_market_context
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    SupabaseTrackedEventRepository,
)


def acquire_and_persist_pre_event_market_context(
    repository: SupabaseTrackedEventRepository,
    *,
    event_id: str,
    ticker: str,
    event_trading_date: date,
    last_confirmed_closed_session_date: date,
    previous_confirmed_closed_session_date: date,
    market_timezone: str,
    actor: str,
    fetcher: DailyOhlcvFetcher = fetch_ohlcv,
) -> PersistentTrackedEvent:
    """Acquire and persist pre-event context from caller-grounded session inputs.

    The caller remains responsible for resolving the market timezone, event
    trading date, and the two confirmed closed session dates. This orchestration
    layer deliberately performs no exchange/calendar/session inference.
    """
    context = acquire_pre_event_market_context(
        ticker=ticker,
        event_trading_date=event_trading_date,
        last_confirmed_closed_session_date=last_confirmed_closed_session_date,
        previous_confirmed_closed_session_date=previous_confirmed_closed_session_date,
        fetcher=fetcher,
    )
    return capture_pre_event_market_context(
        repository,
        event_id=event_id,
        snapshot=context.to_dict(),
        market_timezone=market_timezone,
        actor=actor,
    )
