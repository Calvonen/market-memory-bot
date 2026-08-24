from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pandas as pd

from market_memory.data import fetch_ohlcv
from trading_system.previous_trading_day import (
    PreEventMarketContext,
    pre_event_market_context,
)


DailyOhlcvFetcher = Callable[[str, str, str], pd.DataFrame]


def acquire_pre_event_market_context(
    *,
    ticker: str,
    event_trading_date: date,
    last_confirmed_closed_session_date: date,
    previous_confirmed_closed_session_date: date,
    fetcher: DailyOhlcvFetcher = fetch_ohlcv,
) -> PreEventMarketContext:
    """Fetch Yahoo daily OHLCV for two independently confirmed closed sessions.

    The caller owns the exchange calendar/session-close decision and supplies the
    exact latest and immediately preceding closed session dates. This adapter
    never infers exchange timezone, holidays, weekends, or close time. Both
    required session rows must be present; stale substitution fails closed.
    """

    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker is required")
    if previous_confirmed_closed_session_date >= last_confirmed_closed_session_date:
        raise ValueError("previous confirmed session must precede last confirmed session")

    daily_ohlcv = fetcher(normalized_ticker, "1mo", "1d")
    if not isinstance(daily_ohlcv.index, pd.DatetimeIndex):
        raise ValueError("daily_ohlcv index must be a DatetimeIndex")
    if daily_ohlcv.index.tz is not None:
        raise ValueError("daily_ohlcv index must use timezone-naive market session dates")
    if daily_ohlcv.index.hasnans:
        raise ValueError("daily_ohlcv index must not contain missing session timestamps")

    latest_session = pd.Timestamp(last_confirmed_closed_session_date)
    previous_session = pd.Timestamp(previous_confirmed_closed_session_date)
    if latest_session not in daily_ohlcv.index:
        raise ValueError("confirmed closed session data is missing")
    if previous_session not in daily_ohlcv.index:
        raise ValueError("previous confirmed closed session data is missing")

    confirmed = daily_ohlcv.loc[daily_ohlcv.index <= latest_session]
    context = pre_event_market_context(
        confirmed,
        event_trading_date=event_trading_date,
    )
    if context is None:
        raise ValueError("confirmed closed session history is incomplete")
    if context.session_date != last_confirmed_closed_session_date:
        raise ValueError("confirmed closed session data is missing")
    if context.previous_session_date != previous_confirmed_closed_session_date:
        raise ValueError("previous confirmed closed session is not immediately preceding")
    return context
