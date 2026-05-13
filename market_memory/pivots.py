from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

PivotType = Literal["peak", "bottom"]


@dataclass(frozen=True)
class Pivot:
    index: pd.Timestamp
    pivot_type: PivotType
    pivot_move_pct: float


def detect_pivots(
    df: pd.DataFrame,
    window: int = 5,
    rsi_low: float = 30,
    rsi_high: float = 70,
    dip_threshold_pct: float = 2.0,
    peak_threshold_pct: float = 2.0,
    min_atr_pct: float = 0.0,
    max_atr_pct: float = 999.0,
    pivot_mode: Literal["all", "bottom", "peak"] = "all",
) -> list[Pivot]:
    """Detect local peaks/bottoms using symmetric rolling windows."""
    pivots: list[Pivot] = []
    highs = df["High"]
    lows = df["Low"]
    closes = df["Close"]

    for i in range(window, len(df) - window):
        sl = slice(i - window, i + window + 1)
        center_idx = df.index[i]
        atr_ok = min_atr_pct <= float(df["atr_pct"].iloc[i]) <= max_atr_pct
        if not atr_ok:
            continue

        local_high = float(highs.iloc[sl].max())
        local_low = float(lows.iloc[sl].min())
        local_range = max(local_high - local_low, 1e-9)
        close = float(closes.iloc[i])
        move_pct = (local_range / close) * 100
        rsi = float(df["rsi"].iloc[i])

        if pivot_mode in {"all", "peak"} and highs.iloc[i] == local_high and rsi >= rsi_high and move_pct >= peak_threshold_pct:
            pivots.append(Pivot(index=center_idx, pivot_type="peak", pivot_move_pct=move_pct))
        if pivot_mode in {"all", "bottom"} and lows.iloc[i] == local_low and rsi <= rsi_low and move_pct >= dip_threshold_pct:
            pivots.append(Pivot(index=center_idx, pivot_type="bottom", pivot_move_pct=move_pct))

    return sorted(pivots, key=lambda p: p.index)
