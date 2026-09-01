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

    def test_swiss_life_shaped_two_hop_discovery_delegates_to_results_provider(self):
        http_get = MagicMock(return_value=_Response({"weburl": "https://www.swisslife.com/"}))
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
        http_get.assert_called_once()
        self.assertEqual(http_get.call_args.kwargs["params"]["symbol"], "SLHN.ZU")
        source = for_event.call_args.args[0]
        self.assertEqual(source.source_kind, "results_page")
        self.assertEqual(
            source.source_url,
            "https://www.swisslife.com/en/home/investors/results-and-reports.html",
        )
        delegated.discover.assert_called_once_with(EVENT_ID)

    def test_non_https_profile_website_fails_closed_without_navigation(self):
        provider = FinnhubOfficialResultsProvider(
            event_id=EVENT_ID,
            ticker="SLHN.ZU",
            scheduled_date=date(2026, 9, 1),
            api_key="test-key",
            http_get=MagicMock(return_value=_Response({"weburl": "http://www.swisslife.com/"})),
        )
        provider._fetch_html = MagicMock()

        self.assertIsNone(provider.discover(EVENT_ID))
        self.assertIn("official HTTPS company website", provider.describe_no_release() or "")
        provider._fetch_html.assert_not_called()


if __name__ == "__main__":
    unittest.main()
