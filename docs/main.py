from __future__ import annotations

import argparse

from market_memory.config import SECTOR_SETTINGS
from market_memory.data import fetch_ohlcv
from market_memory.indicators import add_indicators
from market_memory.pivots import detect_pivots
from market_memory.similarity import find_best_matches
from market_memory.visualization import plot_overlay


def run_market_memory(
    ticker: str = "AAPL",
    period: str = "5y",
    similarity_alert: float = 0.75,
    sector: str = "teknologia",
    pivot_mode: str = "all",
) -> None:
    raw = fetch_ohlcv(ticker=ticker, period=period)
    enriched = add_indicators(raw)
    settings = SECTOR_SETTINGS[sector]

    pivots = detect_pivots(
        enriched,
        window=5,
        rsi_low=settings.rsi_low,
        rsi_high=settings.rsi_high,
        dip_threshold_pct=settings.dip_threshold_pct,
        peak_threshold_pct=settings.peak_threshold_pct,
        min_atr_pct=settings.min_atr_pct,
        max_atr_pct=settings.max_atr_pct,
        pivot_mode=pivot_mode,
    )
    matches = find_best_matches(enriched, pivots, current_window=15, top_k=8)

    print(f"Ticker: {ticker} | Sector: {sector} | Mode: {pivot_mode}")
    print("ticker | similarity | pivot date | pivot type | alert status | historical return after pivot")

    for match in matches:
        alert = "REBOUND WATCH" if match.pivot.pivot_type == "bottom" else "SHORT WATCH"
        is_alert = match.score >= similarity_alert
        status = "ALERT" if is_alert else "INFO"
        print(
            f"{ticker} | {match.score:.3f} | {match.pivot.index.date()} | {match.pivot.pivot_type:<6} | "
            f"{status}:{alert if is_alert else 'no alert'} | {match.historical_return_after_pivot:+.2f}%"
        )

    current = enriched.iloc[-15:]
    fig = plot_overlay(current=current, matches=matches)
    fig.update_layout(
        legend_title_text=f"Similarity score shown per match ({sector})",
    )
    fig.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", nargs="?", default="AAPL")
    parser.add_argument("--sector", choices=list(SECTOR_SETTINGS.keys()), default="teknologia")
    parser.add_argument("--mode", choices=["all", "bottom", "peak"], default="all")
    args = parser.parse_args()
    run_market_memory(ticker=args.ticker, sector=args.sector, pivot_mode=args.mode)
