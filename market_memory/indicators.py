from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange


def add_indicators(df: pd.DataFrame, rsi_window: int = 14) -> pd.DataFrame:
    data = df.copy()

    data["rsi"] = RSIIndicator(close=data["Close"], window=rsi_window).rsi()

    macd = MACD(close=data["Close"])
    data["macd_hist"] = macd.macd_diff()

    atr = AverageTrueRange(
        high=data["High"],
        low=data["Low"],
        close=data["Close"],
    ).average_true_range()
    data["atr_pct"] = (atr / data["Close"]) * 100

    vol_ma20 = data["Volume"].rolling(20).mean()
    data["volume_ratio"] = data["Volume"] / vol_ma20

    ema20 = EMAIndicator(close=data["Close"], window=20).ema_indicator()
    ema50 = EMAIndicator(close=data["Close"], window=50).ema_indicator()
    data["dist_ema20"] = (data["Close"] - ema20) / ema20
    data["dist_ema50"] = (data["Close"] - ema50) / ema50

    return data.dropna().copy()
