import unittest
from datetime import date

from trading_system.calendar_provider import CalendarCandidate, FinnhubEarningsCalendarProvider


class _FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = str(payload)

    def json(self):
        return self._payload


class FinnhubEarningsCalendarProviderMappingTests(unittest.TestCase):
    def _provider(self, payload, *, captured: dict | None = None) -> FinnhubEarningsCalendarProvider:
        def fake_get(url, params=None, timeout=None):
            if captured is not None:
                captured["url"] = url
                captured["params"] = params
                captured["timeout"] = timeout
            return _FakeResponse(payload)

        return FinnhubEarningsCalendarProvider(api_key="test-finnhub-key", http_get=fake_get)

    def test_maps_a_well_formed_finnhub_row_into_a_calendar_candidate(self) -> None:
        provider = self._provider(
            {
                "earningsCalendar": [
                    {
                        "symbol": "AAPL",
                        "date": "2026-10-29",
                        "name": "Apple Inc",
                        "exchange": "NASDAQ",
                    }
                ]
            }
        )

        candidates = provider.fetch_upcoming(date(2026, 8, 1), date(2026, 12, 1))

        self.assertEqual(
            candidates,
            (
                CalendarCandidate(
                    company_name="Apple Inc",
                    instrument="AAPL",
                    market="NASDAQ",
                    event_type="earnings",
                    scheduled_date=date(2026, 10, 29),
                    source="finnhub",
                ),
            ),
        )

    def test_falls_back_to_symbol_and_unknown_market_when_finnhub_omits_them(self) -> None:
        # Finnhub's earnings-calendar endpoint does not reliably include a
        # display name or exchange/country - a row missing everything but
        # symbol+date must still be usable, not rejected.
        provider = self._provider({"earningsCalendar": [{"symbol": "HAS.L", "date": "2026-08-20"}]})

        candidates = provider.fetch_upcoming(date(2026, 8, 1), date(2026, 9, 1))

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.company_name, "HAS.L")
        self.assertEqual(candidate.instrument, "HAS.L")
        self.assertEqual(candidate.market, "Unknown")
        self.assertEqual(candidate.event_type, "earnings")
        self.assertEqual(candidate.source, "finnhub")

    def test_drops_rows_missing_symbol_or_date_without_failing_the_whole_batch(self) -> None:
        provider = self._provider(
            {
                "earningsCalendar": [
                    {"symbol": "AAPL", "date": "2026-10-29"},
                    {"symbol": None, "date": "2026-10-30"},
                    {"symbol": "MSFT", "date": None},
                    {"symbol": "GOOG", "date": "not-a-date"},
                ]
            }
        )

        candidates = provider.fetch_upcoming(date(2026, 8, 1), date(2026, 12, 1))

        self.assertEqual([c.instrument for c in candidates], ["AAPL"])

    def test_missing_earnings_calendar_key_yields_no_candidates(self) -> None:
        provider = self._provider({})

        candidates = provider.fetch_upcoming(date(2026, 8, 1), date(2026, 9, 1))

        self.assertEqual(candidates, ())

    def test_requests_the_from_to_range_and_the_api_key_as_token(self) -> None:
        captured: dict = {}
        provider = self._provider({"earningsCalendar": []}, captured=captured)

        provider.fetch_upcoming(date(2026, 8, 1), date(2026, 8, 31))

        self.assertEqual(captured["params"]["from"], "2026-08-01")
        self.assertEqual(captured["params"]["to"], "2026-08-31")
        self.assertEqual(captured["params"]["token"], "test-finnhub-key")
        self.assertIn("/calendar/earnings", captured["url"])

    def test_non_ok_response_raises_a_runtime_error(self) -> None:
        provider = self._provider(None)
        provider._http_get = lambda *a, **k: _FakeResponse({"error": "oops"}, status_code=500)

        with self.assertRaises(RuntimeError):
            provider.fetch_upcoming(date(2026, 8, 1), date(2026, 9, 1))

    def test_missing_api_key_raises_immediately(self) -> None:
        import os

        previous = os.environ.pop("FINNHUB_API_KEY", None)
        try:
            with self.assertRaises(RuntimeError):
                FinnhubEarningsCalendarProvider()
        finally:
            if previous is not None:
                os.environ["FINNHUB_API_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
