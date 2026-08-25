from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from trading_system.release_ingestion import ReleaseDocument
from trading_system.sec_release_ingestion import SecEdgarResultsProvider


class SameDaySecFilingTests(unittest.TestCase):
    def setUp(self) -> None:
        SecEdgarResultsProvider.clear_company_ticker_cache()
        self.provider = SecEdgarResultsProvider(
            ticker="DKS",
            scheduled_date=date(2026, 8, 25),
            user_agent="MarketAI test@example.invalid",
        )

    def test_recent_and_overlapping_historical_filings_are_merged(self) -> None:
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": ["2026-08-25"],
                    "accessionNumber": ["0001089063-26-000100"],
                    "primaryDocument": ["unrelated.htm"],
                    "acceptanceDateTime": ["2026-08-25T18:00:00Z"],
                },
                "files": [{
                    "name": "CIK0001089063-submissions-001.json",
                    "filingFrom": "2026-01-01",
                    "filingTo": "2026-08-25",
                }],
            }
        }
        historical = {
            "form": ["8-K"],
            "filingDate": ["2026-08-25"],
            "accessionNumber": ["0001089063-26-000099"],
            "primaryDocument": ["earnings.htm"],
            "acceptanceDateTime": ["2026-08-25T07:00:00Z"],
        }
        tickers = {"0": {"cik_str": 1089063, "ticker": "DKS"}}
        expected = ReleaseDocument(
            event_id="calendar:event-id",
            source_type="company_results",
            source_url="https://www.sec.gov/Archives/edgar/data/1089063/000108906326000099/ex991.htm",
            source_title="EX-99.1 earnings",
            raw_text="earnings release " * 100,
        )

        with patch.object(
            self.provider,
            "_fetch_json",
            side_effect=[tickers, submissions, historical],
        ), patch.object(
            self.provider,
            "_discover_from_filing",
            side_effect=[None, expected],
        ) as discover_filing:
            result = self.provider.discover("calendar:event-id")

        self.assertEqual(result, expected)
        self.assertEqual(discover_filing.call_count, 2)

    def test_failed_same_day_filing_does_not_starve_later_filing(self) -> None:
        tickers = {"0": {"cik_str": 1089063, "ticker": "DKS"}}
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K", "8-K"],
                    "filingDate": ["2026-08-25", "2026-08-25"],
                    "accessionNumber": [
                        "0001089063-26-000100",
                        "0001089063-26-000099",
                    ],
                    "primaryDocument": ["broken.htm", "earnings.htm"],
                    "acceptanceDateTime": [
                        "2026-08-25T18:00:00Z",
                        "2026-08-25T07:00:00Z",
                    ],
                }
            }
        }
        expected = ReleaseDocument(
            event_id="calendar:event-id",
            source_type="company_results",
            source_url="https://www.sec.gov/Archives/edgar/data/1089063/000108906326000099/ex991.htm",
            source_title="EX-99.1 earnings",
            raw_text="financial results " * 100,
        )

        with patch.object(
            self.provider,
            "_fetch_json",
            side_effect=[tickers, submissions],
        ), patch.object(
            self.provider,
            "_discover_from_filing",
            side_effect=[RuntimeError("broken filing"), expected],
        ) as discover_filing:
            result = self.provider.discover("calendar:event-id")

        self.assertEqual(result, expected)
        self.assertEqual(discover_filing.call_count, 2)


if __name__ == "__main__":
    unittest.main()
