import unittest
from datetime import date

from trading_system.calendar_provider import FinnhubEarningsCalendarProvider


class _FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = str(payload)

    def json(self):
        return self._payload


class CalendarCompanyNameEnrichmentTests(unittest.TestCase):
    def test_bulk_us_symbol_metadata_enriches_calendar_ticker_name_and_market(self) -> None:
        calls: list[str] = []

        def fake_get(url, params=None, timeout=None):
            calls.append(url)
            if url.endswith('/calendar/earnings'):
                return _FakeResponse(
                    {
                        'earningsCalendar': [
                            {'symbol': 'DKS', 'date': '2026-08-24', 'year': 2026, 'quarter': 2},
                            {'symbol': 'BTM', 'date': '2026-08-24', 'year': 2026, 'quarter': 2},
                        ]
                    }
                )
            if url.endswith('/stock/symbol'):
                self.assertEqual(params['exchange'], 'US')
                return _FakeResponse(
                    [
                        {'symbol': 'DKS', 'description': "DICK'S SPORTING GOODS INC"},
                        {'symbol': 'BTM', 'description': 'BITCOIN DEPOT INC'},
                    ]
                )
            raise AssertionError(f'unexpected URL {url}')

        provider = FinnhubEarningsCalendarProvider(api_key='test-key', http_get=fake_get)

        candidates = provider.fetch_upcoming(date(2026, 8, 22), date(2026, 9, 22))

        self.assertEqual([candidate.company_name for candidate in candidates], [
            "DICK'S SPORTING GOODS INC",
            'BITCOIN DEPOT INC',
        ])
        self.assertTrue(all(candidate.market == 'USA' for candidate in candidates))
        self.assertEqual(sum(url.endswith('/stock/symbol') for url in calls), 1)

    def test_metadata_failure_does_not_break_calendar_sync(self) -> None:
        def fake_get(url, params=None, timeout=None):
            if url.endswith('/calendar/earnings'):
                return _FakeResponse(
                    {'earningsCalendar': [{'symbol': 'DKS', 'date': '2026-08-24'}]}
                )
            if url.endswith('/stock/symbol'):
                return _FakeResponse({'error': 'rate limited'}, status_code=429)
            raise AssertionError(f'unexpected URL {url}')

        provider = FinnhubEarningsCalendarProvider(api_key='test-key', http_get=fake_get)

        candidates = provider.fetch_upcoming(date(2026, 8, 22), date(2026, 9, 22))

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].company_name, 'DKS')
        self.assertEqual(candidates[0].market, 'Unknown')


if __name__ == '__main__':
    unittest.main()
