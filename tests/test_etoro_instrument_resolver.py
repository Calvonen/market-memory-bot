import unittest

from trading_system.etoro_instrument_resolver import (
    EtoroInstrumentResolver,
    InstrumentResolutionRequest,
)
from trading_system.etoro_market_data import EtoroInstrumentCandidate, EtoroMarketDataProvider


class _FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = str(payload)

    def json(self):
        return self._payload


class _SearchStub:
    def __init__(self, results: dict[str, tuple[EtoroInstrumentCandidate, ...]]) -> None:
        self.results = results
        self.calls: list[str] = []

    def search_instruments(self, term: str) -> tuple[EtoroInstrumentCandidate, ...]:
        self.calls.append(term)
        return self.results.get(term, ())


class EtoroInstrumentSearchTests(unittest.TestCase):
    def test_search_uses_documented_search_parameter_and_maps_candidates(self) -> None:
        captured: dict = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured.update(url=url, params=params, headers=headers, timeout=timeout)
            return _FakeResponse(
                {
                    "data": [
                        {
                            "instrumentId": 2619,
                            "symbol": "HAS.L",
                            "displayName": "Hays PLC",
                            "exchangeName": "London Stock Exchange",
                        }
                    ]
                }
            )

        provider = EtoroMarketDataProvider(
            api_key="api-secret",
            user_key="user-secret",
            http_get=fake_get,
        )

        results = provider.search_instruments("Hays")

        self.assertTrue(captured["url"].endswith("/search"))
        self.assertEqual(captured["params"], {"search": "Hays"})
        self.assertNotIn("query", captured["params"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].instrument_id, 2619)
        self.assertEqual(results[0].symbol, "HAS.L")
        self.assertEqual(results[0].display_name, "Hays PLC")
        self.assertEqual(results[0].market, "London Stock Exchange")

    def test_search_fails_closed_when_response_shape_is_missing_data(self) -> None:
        provider = EtoroMarketDataProvider(
            api_key="api-secret",
            user_key="user-secret",
            http_get=lambda *a, **k: _FakeResponse({"unexpected": []}),
        )

        with self.assertRaises(RuntimeError):
            provider.search_instruments("AAPL")


class EtoroInstrumentResolverTests(unittest.TestCase):
    def test_exact_symbol_match_wins(self) -> None:
        stub = _SearchStub(
            {
                "AAPL": (
                    EtoroInstrumentCandidate(1001, "AAPL", "Apple Inc", "NASDAQ"),
                    EtoroInstrumentCandidate(9999, "AAPL.MX", "Apple Inc", "Mexico"),
                ),
                "Apple Inc": (),
            }
        )
        resolver = EtoroInstrumentResolver(stub)

        resolved = resolver.resolve(InstrumentResolutionRequest("AAPL", "Apple Inc", "US"))

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.instrument_id, 1001)
        self.assertEqual(resolved.symbol, "AAPL")

    def test_unique_base_symbol_can_resolve_suffix_variant(self) -> None:
        stub = _SearchStub(
            {
                "NOKIA.HE": (EtoroInstrumentCandidate(2001, "NOKIA", "Nokia Oyj", "Helsinki"),),
                "Nokia Oyj": (),
            }
        )
        resolver = EtoroInstrumentResolver(stub)

        resolved = resolver.resolve(InstrumentResolutionRequest("NOKIA.HE", "Nokia Oyj", "FI"))

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.instrument_id, 2001)

    def test_ambiguous_base_symbol_fails_closed_without_exact_name(self) -> None:
        candidates = (
            EtoroInstrumentCandidate(1, "ABC", "ABC One", "US"),
            EtoroInstrumentCandidate(2, "ABC.L", "ABC Two", "London"),
        )
        stub = _SearchStub({"ABC.DE": candidates, "ABC AG": candidates})
        resolver = EtoroInstrumentResolver(stub)

        resolved = resolver.resolve(InstrumentResolutionRequest("ABC.DE", "ABC AG", "DE"))

        self.assertIsNone(resolved)

    def test_exact_company_name_can_disambiguate_base_symbol_collision(self) -> None:
        candidates = (
            EtoroInstrumentCandidate(1, "ABC", "ABC Holdings Inc", "US"),
            EtoroInstrumentCandidate(2, "ABC.L", "ABC PLC", "London"),
        )
        stub = _SearchStub({"ABC.DE": candidates, "ABC PLC": candidates})
        resolver = EtoroInstrumentResolver(stub)

        resolved = resolver.resolve(InstrumentResolutionRequest("ABC.DE", "ABC PLC", "UK"))

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.instrument_id, 2)

    def test_unique_exact_company_name_is_safe_fallback(self) -> None:
        hays = EtoroInstrumentCandidate(2619, None, "Hays PLC", "London Stock Exchange")
        stub = _SearchStub({"HAS.L": (), "Hays PLC": (hays,)})
        resolver = EtoroInstrumentResolver(stub)

        resolved = resolver.resolve(InstrumentResolutionRequest("HAS.L", "Hays PLC", "UK"))

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.instrument_id, 2619)
        self.assertEqual(resolved.display_name, "Hays PLC")

    def test_partial_or_fuzzy_name_is_not_guessed(self) -> None:
        stub = _SearchStub(
            {
                "NOPE": (),
                "XYZ Group": (EtoroInstrumentCandidate(10, "XYZ", "XYZ Holdings PLC", "London"),),
            }
        )
        resolver = EtoroInstrumentResolver(stub)

        resolved = resolver.resolve(InstrumentResolutionRequest("NOPE", "XYZ Group", "UK"))

        self.assertIsNone(resolved)

    def test_exact_symbol_match_fails_closed_when_company_name_conflicts(self) -> None:
        stub = _SearchStub(
            {
                "AAPL": (EtoroInstrumentCandidate(1001, "AAPL", "Apple Inc", "NASDAQ"),),
                "Totally Different Corp": (),
            }
        )
        resolver = EtoroInstrumentResolver(stub)

        resolved = resolver.resolve(InstrumentResolutionRequest("AAPL", "Totally Different Corp", "US"))

        self.assertIsNone(resolved)

    def test_base_symbol_match_fails_closed_when_exchange_suffix_conflicts(self) -> None:
        stub = _SearchStub(
            {
                "ABC.L": (EtoroInstrumentCandidate(5, "ABC.DE", "ABC AG", "Frankfurt Stock Exchange"),),
            }
        )
        resolver = EtoroInstrumentResolver(stub)

        resolved = resolver.resolve(InstrumentResolutionRequest("ABC.L", "", "UK"))

        self.assertIsNone(resolved)

    def test_base_symbol_match_resolves_when_company_and_market_agree(self) -> None:
        stub = _SearchStub(
            {
                "RMV.L": (
                    EtoroInstrumentCandidate(777, "RMV", "Rightmove Group PLC", "London Stock Exchange"),
                ),
                "Rightmove Group PLC": (),
            }
        )
        resolver = EtoroInstrumentResolver(stub)

        resolved = resolver.resolve(InstrumentResolutionRequest("RMV.L", "Rightmove Group PLC", "UK"))

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.instrument_id, 777)


if __name__ == "__main__":
    unittest.main()
