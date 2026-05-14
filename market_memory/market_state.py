from __future__ import annotations

import pandas as pd


ATR_AVG_WINDOW = 60


def _fmt_float(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}"


def get_current_market_state(df: pd.DataFrame) -> list[dict[str, str]]:
    if df.empty:
        return []

    current = df.iloc[-1]
    previous = df.iloc[-2] if len(df) > 1 else current

    rows: list[dict[str, str]] = []

    rsi = float(current["rsi"])
    if rsi < 30:
        rsi_state = f"🟢 Oversold (RSI {_fmt_float(rsi)})"
    elif rsi > 70:
        rsi_state = f"🔴 Overbought (RSI {_fmt_float(rsi)})"
    else:
        rsi_state = f"⚪ Neutral (RSI {_fmt_float(rsi)})"
    rows.append({"Mittari": "RSI", "Tila": rsi_state})

    atr_pct = float(current["atr_pct"])
    atr_pct_avg_60d = float(df["atr_pct"].tail(ATR_AVG_WINDOW).mean())
    if atr_pct > 1.3 * atr_pct_avg_60d:
        vol_state = f"🟠 High (ATR% {_fmt_float(atr_pct)} vs avg {_fmt_float(atr_pct_avg_60d)})"
    elif atr_pct < 0.8 * atr_pct_avg_60d:
        vol_state = f"🔵 Low (ATR% {_fmt_float(atr_pct)} vs avg {_fmt_float(atr_pct_avg_60d)})"
    else:
        vol_state = f"⚪ Normal (ATR% {_fmt_float(atr_pct)} vs avg {_fmt_float(atr_pct_avg_60d)})"
    rows.append({"Mittari": "Volatiliteetti", "Tila": vol_state})

    close = float(current["Close"])
    ema20 = close / (1.0 + float(current["dist_ema20"]))
    ema50 = close / (1.0 + float(current["dist_ema50"]))
    if close > ema20 > ema50:
        trend_state = "🟢 Bullish (Close > EMA20 > EMA50)"
    elif close < ema20 < ema50:
        trend_state = "🔴 Bearish (Close < EMA20 < EMA50)"
    else:
        trend_state = "🟠 Mixed (Close/EMA20/EMA50 ristiriita)"
    rows.append({"Mittari": "Trendi", "Tila": trend_state})

    volume_ratio = float(current["volume_ratio"])
    if volume_ratio > 1.5:
        volume_state = f"🟢 Elevated ({_fmt_float(volume_ratio)}x avg volume)"
    elif volume_ratio < 0.7:
        volume_state = f"🔵 Low ({_fmt_float(volume_ratio)}x avg volume)"
    else:
        volume_state = f"⚪ Normal ({_fmt_float(volume_ratio)}x avg volume)"
    rows.append({"Mittari": "Volyymi", "Tila": volume_state})

    macd_hist = float(current["macd_hist"])
    prev_macd_hist = float(previous["macd_hist"])
    if macd_hist > 0 and macd_hist > prev_macd_hist:
        momentum_state = f"🟢 Strengthening (MACD hist {_fmt_float(macd_hist, 3)} ↑)"
    elif macd_hist < 0 and macd_hist < prev_macd_hist:
        momentum_state = f"🔴 Weakening (MACD hist {_fmt_float(macd_hist, 3)} ↓)"
    else:
        momentum_state = f"🟠 Weak / Mixed (MACD hist {_fmt_float(macd_hist, 3)})"
    rows.append({"Mittari": "Momentum", "Tila": momentum_state})

    return rows
