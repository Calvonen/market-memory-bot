import threading
import time

from market_memory import analysis


def test_scan_market_is_bounded_concurrent_and_fail_soft(monkeypatch):
    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    monkeypatch.setitem(analysis.MARKET_TICKERS, "TEST", tickers)

    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_analyze_ticker(ticker: str):
        nonlocal active, max_active

        with lock:
            active += 1
            max_active = max(max_active, active)

        try:
            time.sleep(0.05)
            if ticker == "CCC":
                raise RuntimeError("boom")

            return {
                "price": 100.0,
                "result": {"direction": "LONG"},
                "analog_matches": [{"score": 0.9}],
                "trend": "UP",
            }
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(analysis, "analyze_ticker", fake_analyze_ticker)

    result = analysis.scan_market("TEST", limit=5)

    assert 1 < max_active <= 4
    assert [row["ticker"] for row in result["results"]] == [
        "AAA",
        "BBB",
        "DDD",
        "EEE",
    ]
    assert result["partial"] is True
