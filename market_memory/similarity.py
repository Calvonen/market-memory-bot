from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

from market_memory.pivots import Pivot

FEATURE_COLUMNS = ["rsi", "macd_hist", "atr_pct", "volume_ratio", "dist_ema20", "dist_ema50"]

SIMILARITY_WEIGHTS = {
    "price": 0.25,
    "rsi": 0.20,
    "volume": 0.20,
    "volatility": 0.15,
    "trend": 0.20,
}


def normalize_similarity_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        return SIMILARITY_WEIGHTS.copy()
    return {name: value / total for name, value in weights.items()}


@dataclass
class MatchResult:
    pivot: Pivot
    score: float
    price_similarity: float
    rsi_similarity: float
    volume_similarity: float
    volatility_similarity: float
    trend_similarity: float
    historical_window: pd.DataFrame
    historical_pre_window: pd.DataFrame
    historical_post_window: pd.DataFrame
    historical_return_after_pivot: float
    return_plus_5d: float | None
    return_plus_10d: float | None
    return_plus_15d: float | None


def _flatten_features(window_df: pd.DataFrame) -> np.ndarray:
    return window_df[FEATURE_COLUMNS].to_numpy().flatten()


def _safe_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if np.allclose(a, 0) and np.allclose(b, 0):
        return 1.0
    return float(cosine_similarity(a.reshape(1, -1), b.reshape(1, -1))[0][0])


def _component_similarities(current: pd.DataFrame, historical: pd.DataFrame) -> dict[str, float]:
    current_close_returns = current["Close"].pct_change().dropna().to_numpy()
    hist_close_returns = historical["Close"].pct_change().dropna().to_numpy()

    price_similarity = _safe_similarity(current_close_returns, hist_close_returns)
    rsi_similarity = _safe_similarity(current["rsi"].to_numpy(), historical["rsi"].to_numpy())
    volume_similarity = _safe_similarity(current["volume_ratio"].to_numpy(), historical["volume_ratio"].to_numpy())
    volatility_similarity = _safe_similarity(current["atr_pct"].to_numpy(), historical["atr_pct"].to_numpy())

    current_trend = current[["dist_ema20", "dist_ema50"]].to_numpy().flatten()
    hist_trend = historical[["dist_ema20", "dist_ema50"]].to_numpy().flatten()
    trend_similarity = _safe_similarity(current_trend, hist_trend)

    return {
        "price": price_similarity,
        "rsi": rsi_similarity,
        "volume": volume_similarity,
        "volatility": volatility_similarity,
        "trend": trend_similarity,
    }


def find_best_matches(
    df: pd.DataFrame,
    pivots: list[Pivot],
    current_window: int = 15,
    top_k: int = 5,
    post_pivot_window: int = 15,
    similarity_weights: dict[str, float] | None = None,
) -> list[MatchResult]:
    if len(df) < current_window:
        raise ValueError("Not enough rows for current window")

    current = df.iloc[-current_window:]
    current_vec = _flatten_features(current).reshape(1, -1)
    weights = normalize_similarity_weights(similarity_weights or SIMILARITY_WEIGHTS)

    matches: list[MatchResult] = []

    for pivot in pivots:
        pivot_loc = df.index.get_loc(pivot.index)
        start = pivot_loc - current_window
        end = pivot_loc + post_pivot_window
        if start < 0:
            continue
        if end >= len(df):
            continue

        hist = df.iloc[start : end + 1]
        if len(hist) != (current_window + post_pivot_window + 1):
            continue

        hist_pre = hist.iloc[:current_window]
        hist_post = hist.iloc[current_window + 1 :]

        hist_vec = _flatten_features(hist_pre).reshape(1, -1)
        base_score = float(cosine_similarity(current_vec, hist_vec)[0][0])
        components = _component_similarities(current=current, historical=hist_pre)
        weighted_score = sum(components[name] * weight for name, weight in weights.items())
        score = (base_score * 0.15) + (weighted_score * 0.85)

        def compute_forward_return(days: int) -> float | None:
            target_loc = pivot_loc + days
            if target_loc >= len(df):
                return None
            pivot_close = float(df["Close"].iloc[pivot_loc])
            target_close = float(df["Close"].iloc[target_loc])
            return ((target_close / pivot_close) - 1.0) * 100

        forward_end = pivot_loc + post_pivot_window
        pivot_close = float(df["Close"].iloc[pivot_loc])
        next_close = float(df["Close"].iloc[forward_end])
        hist_ret = ((next_close / pivot_close) - 1.0) * 100
        matches.append(
            MatchResult(
                pivot=pivot,
                score=score,
                price_similarity=components["price"],
                rsi_similarity=components["rsi"],
                volume_similarity=components["volume"],
                volatility_similarity=components["volatility"],
                trend_similarity=components["trend"],
                historical_window=hist,
                historical_pre_window=hist_pre,
                historical_post_window=hist_post,
                historical_return_after_pivot=hist_ret,
                return_plus_5d=compute_forward_return(5),
                return_plus_10d=compute_forward_return(10),
                return_plus_15d=compute_forward_return(15),
            )
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:top_k]
