from __future__ import annotations

import yfinance as yf
import pandas as pd


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
