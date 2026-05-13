from __future__ import annotations

from market_memory.data import fetch_ohlcv
from market_memory.indicators import add_indicators
from market_memory.pivots import detect_pivots
from market_memory.similarity import find_best_matches
from market_memory.visualization import plot_overlay


def run_market_memory(ticker: str = "AAPL", period: str = "5y", similarity_alert: float = 0.75) -> None:
    raw = fetch_ohlcv(ticker=ticker, period=period)
    enriched = add_indicators(raw)

    pivots = detect_pivots(enriched, window=5)
    matches = find_best_matches(enriched, pivots, current_window=15, top_k=8)

    print(f"Ticker: {ticker}")
    print(f"Pivots found: {len(pivots)}")

    for match in matches:
        alert = "REBOUND WATCH" if match.pivot.pivot_type == "bottom" else "SHORT WATCH"
        is_alert = match.score >= similarity_alert
        status = "ALERT" if is_alert else "INFO"
        print(
            f"[{status}] {match.pivot.index.date()} {match.pivot.pivot_type:<6} "
            f"score={match.score:.3f} -> {alert if is_alert else 'no alert'}"
        )

    current = enriched.iloc[-15:]
    fig = plot_overlay(current=current, matches=matches)
    fig.show()


if __name__ == "__main__":
    run_market_memory()
