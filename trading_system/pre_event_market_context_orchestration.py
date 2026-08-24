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


def _normalise_ticker(value: str) -> str:
    return value.strip().upper()


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
    layer deliberately performs no exchange/calendar/session inference. Before
    acquisition, the supplied ticker is bound to the canonical persisted event
    instrument so another instrument's prices cannot be captured by mistake.
    """
    event = repository.get(event_id)
    if event is None:
        raise RuntimeError(f"tracked event {event_id} was not found")

    normalized_ticker = _normalise_ticker(ticker)
    canonical_instrument = _normalise_ticker(event.instrument)
    if not normalized_ticker or normalized_ticker != canonical_instrument:
        raise ValueError("ticker does not match tracked event instrument")

    context = acquire_pre_event_market_context(
        ticker=normalized_ticker,
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
