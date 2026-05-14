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
    source: Literal["exact", "reversal_zone"] = "exact"


def _as_1d_series(df: pd.DataFrame, column: str) -> pd.Series:
    """Return a guaranteed 1D Series for a column that may be DataFrame-like."""
    data = df[column]
    if isinstance(data, pd.Series):
        return data
    squeezed = data.squeeze("columns")
    if isinstance(squeezed, pd.Series):
        return squeezed

    # If duplicate column names exist, selecting df[column] can still yield a
    # DataFrame after squeezing. Use the first matching column consistently.
    return data.iloc[:, 0]


def detect_pivots(
    df: pd.DataFrame,
    pivot_window: int = 5,
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
    highs = _as_1d_series(df, "High")
    lows = _as_1d_series(df, "Low")
    closes = _as_1d_series(df, "Close")
    rsi_series = _as_1d_series(df, "rsi")
    atr_pct_series = _as_1d_series(df, "atr_pct")

    for i in range(pivot_window, len(df) - pivot_window):
        center_idx = df.index[i]
        atr_ok = min_atr_pct <= float(atr_pct_series.iloc[i]) <= max_atr_pct
        if not atr_ok:
            continue

        local_high = float(highs.iloc[i - pivot_window : i + pivot_window + 1].max())
        local_low = float(lows.iloc[i - pivot_window : i + pivot_window + 1].min())
        local_range = max(local_high - local_low, 1e-9)
        current_high = float(highs.iloc[i])
        current_low = float(lows.iloc[i])
        current_close = float(closes.iloc[i])
        move_pct = (local_range / current_close) * 100
        rsi = float(rsi_series.iloc[i])

        is_peak = current_high == local_high
        is_bottom = current_low == local_low

        if pivot_mode in {"all", "peak"} and is_peak and rsi >= rsi_high and move_pct >= peak_threshold_pct:
            pivots.append(Pivot(index=center_idx, pivot_type="peak", pivot_move_pct=move_pct, source="exact"))
        if pivot_mode in {"all", "bottom"} and is_bottom and rsi <= rsi_low and move_pct >= dip_threshold_pct:
            pivots.append(Pivot(index=center_idx, pivot_type="bottom", pivot_move_pct=move_pct, source="exact"))

    return sorted(pivots, key=lambda p: p.index)


def detect_reversal_zones(
    df: pd.DataFrame,
    pivot_mode: Literal["all", "bottom", "peak"] = "all",
    lookback: int = 20,
    confirm_min: int = 3,
    confirm_max: int = 10,
) -> list[Pivot]:
    pivots: list[Pivot] = []
    closes = _as_1d_series(df, "Close")
    highs = _as_1d_series(df, "High")
    lows = _as_1d_series(df, "Low")
    rsi_series = _as_1d_series(df, "rsi")
    atr_pct = _as_1d_series(df, "atr_pct")

    rolling_low = lows.rolling(lookback).min()
    rolling_high = highs.rolling(lookback).max()
    atr_mean = atr_pct.rolling(lookback).mean()

    last_added_by_type: dict[str, pd.Timestamp] = {}

    for i in range(lookback, len(df) - confirm_max):
        window_close = closes.iloc[i - lookback : i]
        current_close = float(closes.iloc[i])
        current_rsi = float(rsi_series.iloc[i])
        prev_rsi = float(rsi_series.iloc[i - 1])
        curr_atr = float(atr_pct.iloc[i])
        prev_atr = float(atr_pct.iloc[i - 1])
        mean_atr = float(atr_mean.iloc[i]) if not pd.isna(atr_mean.iloc[i]) else curr_atr

        near_20d_low = current_close <= float(rolling_low.iloc[i]) * 1.02
        near_20d_high = current_close >= float(rolling_high.iloc[i]) * 0.98
        downtrend = current_close <= float(window_close.max()) * 0.95
        uptrend = current_close >= float(window_close.min()) * 1.05
        rsi_bottom_ok = current_rsi <= 38 or (current_rsi <= 50 and current_rsi > prev_rsi)
        rsi_peak_ok = current_rsi >= 62 or (current_rsi >= 50 and current_rsi < prev_rsi)
        vol_bottom_ok = curr_atr >= mean_atr * 1.05 or curr_atr < prev_atr
        vol_peak_ok = curr_atr >= mean_atr * 1.05 or curr_atr < prev_atr

        confirm_slice = slice(i + confirm_min, i + confirm_max + 1)
        future_lows = lows.iloc[confirm_slice]
        future_highs = highs.iloc[confirm_slice]
        future_closes = closes.iloc[confirm_slice]

        if pivot_mode in {"all", "bottom"} and downtrend and near_20d_low and rsi_bottom_ok and vol_bottom_ok:
            base_low = float(lows.iloc[i])
            higher_low_formed = not future_lows.empty and float(future_lows.min()) > base_low
            rebound_formed = not future_closes.empty and float(future_closes.max()) >= current_close * 1.02
            if higher_low_formed or rebound_formed:
                zone_slice = slice(i - confirm_min, i + confirm_max + 1)
                zone_low = lows.iloc[zone_slice]
                zone_idx = zone_low.idxmin()
                zone_high = float(highs.iloc[zone_slice].max())
                zone_low_val = float(zone_low.min())
                move_pct = (zone_high - zone_low_val) / max(current_close, 1e-9) * 100
                if last_added_by_type.get("bottom") != zone_idx:
                    pivots.append(Pivot(index=zone_idx, pivot_type="bottom", pivot_move_pct=move_pct, source="reversal_zone"))
                    last_added_by_type["bottom"] = zone_idx

        if pivot_mode in {"all", "peak"} and uptrend and near_20d_high and rsi_peak_ok and vol_peak_ok:
            base_high = float(highs.iloc[i])
            lower_high_formed = not future_highs.empty and float(future_highs.max()) < base_high
            decline_formed = not future_closes.empty and float(future_closes.min()) <= current_close * 0.98
            trend_weakening = float(future_closes.iloc[-1]) < current_close if not future_closes.empty else False
            if lower_high_formed or decline_formed or trend_weakening:
                zone_slice = slice(i - confirm_min, i + confirm_max + 1)
                zone_high = highs.iloc[zone_slice]
                zone_idx = zone_high.idxmax()
                zone_low = float(lows.iloc[zone_slice].min())
                zone_high_val = float(zone_high.max())
                move_pct = (zone_high_val - zone_low) / max(current_close, 1e-9) * 100
                if last_added_by_type.get("peak") != zone_idx:
                    pivots.append(Pivot(index=zone_idx, pivot_type="peak", pivot_move_pct=move_pct, source="reversal_zone"))
                    last_added_by_type["peak"] = zone_idx

    return sorted(pivots, key=lambda p: p.index)
