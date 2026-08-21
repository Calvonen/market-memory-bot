import market_memory.data as data_module


class FakeSearch:
    def __init__(self, quotes):
        self.quotes = quotes


def test_search_symbols_returns_yahoo_matches(monkeypatch):
    quotes = [{"symbol": "aapl", "quoteType": "EQUITY", "shortname": "Apple Inc.", "exchDisp": "NASDAQ"}]
    monkeypatch.setattr(data_module.yf, "Search", lambda *args, **kwargs: FakeSearch(quotes))

    result = data_module.search_symbols("apple")

    assert result == [{"ticker": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"}]


def test_search_symbols_filters_non_equity_quote_types(monkeypatch):
    quotes = [
        {"symbol": "SPY", "quoteType": "ETF", "shortname": "SPDR S&P 500"},
        {"symbol": "BTC-USD", "quoteType": "CRYPTOCURRENCY", "shortname": "Bitcoin"},
        {"symbol": "AAPL", "quoteType": "EQUITY", "shortname": "Apple Inc."},
    ]
    monkeypatch.setattr(data_module.yf, "Search", lambda *args, **kwargs: FakeSearch(quotes))

    result = data_module.search_symbols("a", limit=1)

    assert [item["ticker"] for item in result] == ["AAPL"]


def test_search_symbols_deduplicates_repeated_symbols(monkeypatch):
    quotes = [
        {"symbol": "AAPL", "quoteType": "EQUITY", "shortname": "Apple Inc."},
        {"symbol": "aapl", "quoteType": "EQUITY", "shortname": "Apple Inc. (duplicate)"},
    ]
    monkeypatch.setattr(data_module.yf, "Search", lambda *args, **kwargs: FakeSearch(quotes))

    result = data_module.search_symbols("apple")

    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"


def test_search_symbols_falls_back_to_local_universe_when_yahoo_returns_nothing(monkeypatch):
    monkeypatch.setattr(data_module.yf, "Search", lambda *args, **kwargs: FakeSearch([]))

    result = data_module.search_symbols("NOKIA")

    assert any(item["ticker"] == "NOKIA.HE" for item in result)


def test_search_symbols_falls_back_to_local_universe_when_yahoo_raises(monkeypatch):
    def raise_error(*args, **kwargs):
        raise RuntimeError("Yahoo Finance is unreachable")

    monkeypatch.setattr(data_module.yf, "Search", raise_error)

    result = data_module.search_symbols("NOKIA")

    assert any(item["ticker"] == "NOKIA.HE" for item in result)
