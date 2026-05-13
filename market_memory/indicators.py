from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange


def _to_series(values: pd.Series | pd.DataFrame) -> pd.Series:
    return values.squeeze("columns") if isinstance(values, pd.DataFrame) else values


def add_indicators(df: pd.DataFrame, rsi_window: int = 14) -> pd.DataFrame:
    data = df.copy()

    close = _to_series(data[["Close"]])
    high = _to_series(data[["High"]])
    low = _to_series(data[["Low"]])
    volume = _to_series(data[["Volume"]])

    data["rsi"] = RSIIndicator(close=close, window=rsi_window).rsi()

    macd = MACD(close=close)
    data["macd_hist"] = macd.macd_diff()

    atr = AverageTrueRange(
        high=high,
        low=low,
        close=close,
    ).average_true_range()
    data["atr_pct"] = (atr / close) * 100

    vol_ma20 = volume.rolling(20).mean()
    data["volume_ratio"] = volume / vol_ma20

    ema20 = EMAIndicator(close=close, window=20).ema_indicator()
    ema50 = EMAIndicator(close=close, window=50).ema_indicator()
    data["dist_ema20"] = (close - ema20) / ema20
    data["dist_ema50"] = (close - ema50) / ema50

    return data.dropna().copy()
