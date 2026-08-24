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
    fetcher: DailyOhlcvFetcher = fetch_ohlcv,
) -> PreEventMarketContext | None:
    """Fetch daily Yahoo OHLCV and build the last complete pre-event context.

    The caller supplies the canonical market-local event trading date. This
    adapter intentionally does not infer exchange timezone/session calendars;
    the pure selector enforces that the returned daily index is already made of
    timezone-naive midnight market-session labels.
    """

    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker is required")

    daily_ohlcv = fetcher(normalized_ticker, "1mo", "1d")
    return pre_event_market_context(
        daily_ohlcv,
        event_trading_date=event_trading_date,
    )
