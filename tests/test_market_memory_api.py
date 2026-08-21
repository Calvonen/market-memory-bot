import unittest
from fastapi.testclient import TestClient
from trading_system.api import create_app
from trading_system.event_repository import InMemoryEventExpectationRepository


class MarketMemoryApiTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def analyzer(ticker):
            self.calls.append(ticker)
            return {"ticker": ticker.upper(), "price": 101.5, "latest_market_state": [], "pivots": [], "analog_matches": [{"score": .91}], "trend": "Bullish", "momentum": "Strengthening", "result": {"direction": "LONG"}, "series": []}
        self.client = TestClient(create_app(
            InMemoryEventExpectationRepository(),
            read_api_key="read",
            market_analyzer=analyzer,
            market_scanner=lambda market, limit: {"market": market, "markets": ["Finland Top", "USA Top"], "results": [], "partial": True},
            market_symbol_searcher=lambda query, limit: [{"ticker": "AAPL", "name": "Apple Inc.", "exchange": "Nasdaq"}] if query.lower().startswith("app") else [],
        ))

    def test_analysis_contract_and_core_service_boundary(self):
        response = self.client.get("/api/v1/market-memory/aapl", headers={"X-MarketAI-Key": "read"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["direction"], "LONG")
        self.assertEqual(self.calls, ["aapl"])

    def test_analysis_and_scanner_require_read_key(self):
        self.assertEqual(self.client.get("/api/v1/market-memory/AAPL").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/scanner").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/symbols?q=app").status_code, 401)

    def test_symbol_search_contract(self):
        response = self.client.get("/api/v1/symbols?q=app", headers={"X-MarketAI-Key": "read"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["ticker"], "AAPL")
        self.assertEqual(response.json()[0]["name"], "Apple Inc.")

    def test_scanner_foundation_contract(self):
        response = self.client.get("/api/v1/scanner?market=Finland%20Top", headers={"X-MarketAI-Key": "read"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["partial"])


if __name__ == "__main__":
    unittest.main()


def test_scanner_returns_503_when_all_analyses_fail():
    def failing_scanner(_market: str, _limit: int):
        raise RuntimeError("All scanner analyses failed")

    app = create_app(
        repository=None,
        paper_repository=None,
        read_api_key="read-key",
        market_scanner=failing_scanner,
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/scanner",
        headers={"X-MarketAI-Key": "read-key"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "All scanner analyses failed"
