from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

from market_memory.pivots import Pivot

FEATURE_COLUMNS = ["rsi", "macd_hist", "atr_pct", "volume_ratio", "dist_ema20", "dist_ema50"]


@dataclass
class MatchResult:
    pivot: Pivot
    score: float
    historical_window: pd.DataFrame
    historical_return_after_pivot: float


def _flatten_features(window_df: pd.DataFrame) -> np.ndarray:
    return window_df[FEATURE_COLUMNS].to_numpy().flatten()


def find_best_matches(
    df: pd.DataFrame,
    pivots: list[Pivot],
    current_window: int = 15,
    top_k: int = 5,
    forward_window: int = 10,
) -> list[MatchResult]:
    if len(df) < current_window:
        raise ValueError("Not enough rows for current window")

    current = df.iloc[-current_window:]
    current_vec = _flatten_features(current).reshape(1, -1)

    matches: list[MatchResult] = []

    for pivot in pivots:
        pivot_loc = df.index.get_loc(pivot.index)
        start = pivot_loc - current_window
        end = pivot_loc
        if start < 0:
            continue

        hist = df.iloc[start:end]
        if len(hist) != current_window:
            continue

        hist_vec = _flatten_features(hist).reshape(1, -1)
        score = float(cosine_similarity(current_vec, hist_vec)[0][0])
        forward_end = min(pivot_loc + forward_window, len(df) - 1)
        pivot_close = float(df["Close"].iloc[pivot_loc])
        next_close = float(df["Close"].iloc[forward_end])
        hist_ret = ((next_close / pivot_close) - 1.0) * 100
        matches.append(
            MatchResult(
                pivot=pivot,
                score=score,
                historical_window=hist,
                historical_return_after_pivot=hist_ret,
            )
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:top_k]
