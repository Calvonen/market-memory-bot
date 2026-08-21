from __future__ import annotations

import pandas as pd
import yfinance as yf

from market_memory.universe import MARKET_TICKERS


REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def fetch_ohlcv(ticker: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV data for a ticker from Yahoo Finance."""
    df = yf.download(ticker, period=period, interval=interval, auto_adjust=False, progress=False)
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        # yfinance may return a (Price, Ticker) MultiIndex, e.g. ("Close", "VALMT.HE").
        # Keep only the OHLCV field names so downstream modules receive flat columns.
        df.columns = df.columns.get_level_values(0)

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")

    return df[REQUIRED_COLUMNS].dropna().copy()


def search_symbols(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Search Yahoo symbols by company name/ticker, with local-universe fallback."""
    text = query.strip()
    if not text:
        return []

    capped_limit = max(1, min(limit, 12))
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    try:
        search = yf.Search(
            text,
            max_results=capped_limit,
            news_count=0,
            include_cb=False,
            include_nav_links=False,
            include_research=False,
        )
        quotes = search.quotes or []
    except Exception:
        quotes = []

    for quote in quotes:
        if str(quote.get("quoteType", "")).upper() not in {"EQUITY", ""}:
            continue
        symbol = str(quote.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        name = str(quote.get("shortname") or quote.get("longname") or symbol).strip()
        exchange = str(quote.get("exchDisp") or quote.get("exchange") or "").strip()
        results.append({"ticker": symbol, "name": name, "exchange": exchange})
        seen.add(symbol)
        if len(results) >= capped_limit:
            return results

    needle = text.upper()
    local_symbols = dict.fromkeys(
        ticker
        for tickers in MARKET_TICKERS.values()
        for ticker in tickers
    )
    for symbol in local_symbols:
        if symbol in seen:
            continue
        if symbol.startswith(needle) or needle in symbol:
            results.append({"ticker": symbol, "name": symbol, "exchange": ""})
            seen.add(symbol)
            if len(results) >= capped_limit:
                break

    return results
