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
    fetcher: DailyOhlcvFetcher = fetch_ohlcv,
) -> PreEventMarketContext | None:
    """Fetch daily Yahoo OHLCV and build the last confirmed closed context.

    The caller supplies both the canonical market-local event trading date and
    the last session date that is independently known to be closed. This adapter
    intentionally does not infer exchange timezone, calendar, or close time.
    Yahoo can include the current still-forming ``1d`` bar, so rows newer than
    ``last_confirmed_closed_session_date`` are discarded before selection.
    """

    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker is required")

    daily_ohlcv = fetcher(normalized_ticker, "1mo", "1d")
    if not isinstance(daily_ohlcv.index, pd.DatetimeIndex):
        raise ValueError("daily_ohlcv index must be a DatetimeIndex")
    if daily_ohlcv.index.tz is not None:
        raise ValueError("daily_ohlcv index must use timezone-naive market session dates")
    if daily_ohlcv.index.hasnans:
        raise ValueError("daily_ohlcv index must not contain missing session timestamps")

    confirmed = daily_ohlcv.loc[
        daily_ohlcv.index <= pd.Timestamp(last_confirmed_closed_session_date)
    ]
    return pre_event_market_context(
        confirmed,
        event_trading_date=event_trading_date,
    )
