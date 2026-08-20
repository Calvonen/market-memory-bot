"""Serializable, read-only application service for Market Memory clients."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd

from market_memory.config import SECTOR_SETTINGS
from market_memory.data import fetch_ohlcv
from market_memory.indicators import add_indicators
from market_memory.market_state import get_current_market_state
from market_memory.pivots import detect_pivots
from market_memory.sector_resolver import resolve_sector
from market_memory.similarity import find_best_matches
from market_memory.universe import MARKET_TICKERS


def analyze_ticker(ticker: str) -> dict[str, Any]:
    """Run the same indicator/pivot/analogy core used by Streamlit."""
    symbol = ticker.strip().upper()
    if not symbol or len(symbol) > 20:
        raise ValueError("Invalid ticker")
    raw = fetch_ohlcv(symbol)
    enriched = add_indicators(raw)
    sector, _ = resolve_sector(symbol)
    settings = SECTOR_SETTINGS.get(sector, SECTOR_SETTINGS["yleinen"])
    pivots = detect_pivots(
        enriched, pivot_window=5, rsi_low=settings.rsi_low,
        rsi_high=settings.rsi_high, dip_threshold_pct=settings.dip_threshold_pct,
        peak_threshold_pct=settings.peak_threshold_pct,
        min_atr_pct=settings.min_atr_pct, max_atr_pct=settings.max_atr_pct,
    )
    matches = find_best_matches(enriched, pivots, current_window=15, top_k=8)
    current = enriched.iloc[-1]
    state = get_current_market_state(enriched)
    trend = next((row["Tila"] for row in state if row["Mittari"] == "Trendi"), "-")
    momentum = next((row["Tila"] for row in state if row["Mittari"] == "Momentum"), "-")
    avg_return = pd.Series([m.return_plus_15d for m in matches], dtype="float64").dropna().mean()
    direction = "NEUTRAL" if pd.isna(avg_return) else ("LONG" if avg_return > 0 else "SHORT")

    return {
        "ticker": symbol,
        "price": float(current["Close"]),
        "latest_market_state": state,
        "trend": trend,
        "momentum": momentum,
        "result": {"direction": direction, "average_return_15d": None if pd.isna(avg_return) else round(float(avg_return), 2)},
        "pivots": [
            {"date": p.index.date().isoformat(), "type": p.pivot_type, "move_pct": round(p.pivot_move_pct, 2)}
            for p in pivots[-20:]
        ],
        "analog_matches": [
            {
                "date": m.pivot.index.date().isoformat(), "type": m.pivot.pivot_type,
                "score": round(m.score, 4), "scores": {
                    "price": round(m.price_similarity, 4), "rsi": round(m.rsi_similarity, 4),
                    "volume": round(m.volume_similarity, 4), "volatility": round(m.volatility_similarity, 4),
                    "trend": round(m.trend_similarity, 4),
                },
                "returns": {"5d": m.return_plus_5d, "10d": m.return_plus_10d, "15d": m.return_plus_15d},
            } for m in matches
        ],
        "series": [
            {"date": index.date().isoformat(), "close": round(float(row["Close"]), 4)}
            for index, row in enriched.tail(180).iterrows()
        ],
    }


def scan_market(market: str, limit: int = 10) -> dict[str, Any]:
    """Small first scanner table; full Streamlit parity is intentionally deferred."""
    if market not in MARKET_TICKERS:
        raise ValueError("Unknown market")
    tickers = MARKET_TICKERS[market][: max(1, min(limit, 25))]
    results_by_ticker: dict[str, dict[str, Any]] = {}

    max_workers = min(4, len(tickers))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {
            executor.submit(analyze_ticker, ticker): ticker
            for ticker in tickers
        }

        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                result = future.result()
            except Exception:
                continue

            best = result["analog_matches"][0] if result["analog_matches"] else None
            results_by_ticker[ticker] = {
                "ticker": ticker,
                "price": result["price"],
                "direction": result["result"]["direction"],
                "best_similarity": best["score"] if best else None,
                "trend": result["trend"],
            }

    rows = [
        results_by_ticker[ticker]
        for ticker in tickers
        if ticker in results_by_ticker
    ]

    return {
        "market": market,
        "markets": list(MARKET_TICKERS),
        "results": rows,
        "partial": True,
    }
