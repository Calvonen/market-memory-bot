from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from trading_system.global_release_discovery import (
    FinnhubOfficialResultsProvider,
    _same_origin_links,
    _select_unique_best,
)


EVENT_ID = "tracked:633c9941-8426-4dda-93b8-d829d0d68605"


class _Response:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class GlobalReleaseDiscoveryTests(unittest.TestCase):
    def test_navigation_deduplicates_same_url_and_stays_on_official_origin(self):
        links = _same_origin_links(
            "https://www.example.com/",
            "https://www.example.com/",
            """
            <a href='/investors'>Investors</a>
            <a href='https://www.example.com/investors'>Investor relations</a>
            <a href='https://elsewhere.example/results'>Results</a>
            """,
        )

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].url, "https://www.example.com/investors")
        self.assertIn("Investors", links[0].label)
        self.assertIn("Investor relations", links[0].label)

    def test_ambiguous_equal_results_links_fail_closed(self):
        links = _same_origin_links(
            "https://www.example.com/",
            "https://www.example.com/investors",
            """
            <a href='/investors/half-year-financial-results'>Financial results</a>
            <a href='/investors/full-year-financial-results'>Financial results</a>
            """,
        )

        self.assertIsNone(_select_unique_best(links, stage="results"))

    def test_swiss_life_shaped_suffix_resolution_and_two_hop_discovery(self):
        http_get = MagicMock(
            side_effect=[
                _Response({}),
                _Response({"result": [{"symbol": "SLHN.SW", "description": "Swiss Life Holding AG"}]}),
                _Response({"weburl": "https://www.swisslife.com/"}),
            ]
        )
        provider = FinnhubOfficialResultsProvider(
            event_id=EVENT_ID,
            ticker="SLHN.ZU",
            scheduled_date=date(2026, 9, 1),
            api_key="test-key",
            http_get=http_get,
        )
        provider._fetch_html = MagicMock(
            side_effect=[
                (
                    "<a href='/en/home/investors.html'>Investors</a>",
                    "https://www.swisslife.com/",
                ),
                (
                    "<a href='/en/home/investors/results-and-reports.html'>Results and reports</a>",
                    "https://www.swisslife.com/en/home/investors.html",
                ),
            ]
        )
        document = MagicMock()
        delegated = MagicMock()
        delegated.discover.return_value = document

        with patch(
            "trading_system.global_release_discovery.ResultsPageOfficialReleaseProvider.for_event",
            return_value=delegated,
        ) as for_event:
            result = provider.discover(EVENT_ID)

        self.assertIs(result, document)
        self.assertEqual(http_get.call_count, 3)
        self.assertEqual(http_get.call_args_list[0].kwargs["params"]["symbol"], "SLHN.ZU")
        self.assertEqual(http_get.call_args_list[1].kwargs["params"]["q"], "SLHN")
        self.assertEqual(http_get.call_args_list[2].kwargs["params"]["symbol"], "SLHN.SW")
        source = for_event.call_args.args[0]
        self.assertEqual(source.source_kind, "results_page")
        self.assertEqual(
            source.source_url,
            "https://www.swisslife.com/en/home/investors/results-and-reports.html",
        )
        delegated.discover.assert_called_once_with(EVENT_ID)

    def test_ambiguous_symbol_suffixes_fail_closed_before_navigation(self):
        provider = FinnhubOfficialResultsProvider(
            event_id=EVENT_ID,
            ticker="ABC.ZU",
            scheduled_date=date(2026, 9, 1),
            api_key="test-key",
            http_get=MagicMock(
                side_effect=[
                    _Response({}),
                    _Response({"result": [{"symbol": "ABC.SW"}, {"symbol": "ABC.L"}]}),
                ]
            ),
        )
        provider._fetch_html = MagicMock()

        self.assertIsNone(provider.discover(EVENT_ID))
        provider._fetch_html.assert_not_called()

    def test_non_https_profile_website_fails_closed_without_navigation(self):
        provider = FinnhubOfficialResultsProvider(
            event_id=EVENT_ID,
            ticker="SLHN.ZU",
            scheduled_date=date(2026, 9, 1),
            api_key="test-key",
            http_get=MagicMock(
                side_effect=[
                    _Response({"weburl": "http://www.swisslife.com/"}),
                    _Response({"result": []}),
                ]
            ),
        )
        provider._fetch_html = MagicMock()

        self.assertIsNone(provider.discover(EVENT_ID))
        self.assertIn("official HTTPS company website", provider.describe_no_release() or "")
        provider._fetch_html.assert_not_called()


if __name__ == "__main__":
    unittest.main()
