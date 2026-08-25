from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd

from market_memory.data import fetch_ohlcv
from trading_system.previous_trading_day import (
    PreEventMarketContext,
    pre_event_market_context,
)


DailyOhlcvFetcher = Callable[[str, str, str], pd.DataFrame]


def acquire_pre_event_market_context(
    *,
    provider_symbol: str,
    event_trading_date: date,
    last_confirmed_closed_session_date: date,
    previous_confirmed_closed_session_date: date,
    fetcher: DailyOhlcvFetcher = fetch_ohlcv,
) -> PreEventMarketContext:
    """Fetch provider daily OHLCV for two independently confirmed closed sessions.

    ``provider_symbol`` is the market-data provider's ticker, not the broker
    instrument. Broker and provider are separate namespaces (eToro WDS.ASX is
    Yahoo WDS.AX), so the caller must translate through the grounded market
    profile first. This adapter performs no translation of its own.

    The caller owns the exchange-calendar/session-close decision and supplies
    the exact latest and immediately preceding closed session dates. Both rows
    must be present; stale substitution fails closed.

    ``last_confirmed_closed_session_date`` may equal ``event_trading_date`` for
    a genuine post-close event. It can never be later than the event trading
    date.
    """
    normalized_symbol = provider_symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("provider_symbol is required")
    if previous_confirmed_closed_session_date >= last_confirmed_closed_session_date:
        raise ValueError("previous confirmed session must precede last confirmed session")
    if last_confirmed_closed_session_date > event_trading_date:
        raise ValueError("confirmed closed session must not be after the event trading date")

    daily_ohlcv = fetcher(normalized_symbol, "1mo", "1d")
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
        sessions_before=last_confirmed_closed_session_date + timedelta(days=1),
    )
    if context is None:
        raise ValueError("confirmed closed session history is incomplete")
    if context.session_date != last_confirmed_closed_session_date:
        raise ValueError("confirmed closed session data is missing")
    if context.previous_session_date != previous_confirmed_closed_session_date:
        raise ValueError("previous confirmed closed session is not immediately preceding")
    return context
