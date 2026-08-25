from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

from trading_system.sec_release_ingestion import SecEdgarResultsProvider


class SecEdgarResultsProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        SecEdgarResultsProvider.clear_company_ticker_cache()
        self.provider = SecEdgarResultsProvider(
            ticker="DKS",
            scheduled_date=date(2026, 8, 25),
            user_agent="MarketAI test@example.invalid",
        )
        self.tickers = {
            "0": {"cik_str": 1089063, "ticker": "DKS", "title": "DICKS SPORTING GOODS"}
        }
        self.submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K", "8-K"],
                    "filingDate": ["2026-08-25", "2026-08-24"],
                    "accessionNumber": ["0001089063-26-000099", "0001089063-26-000098"],
                    "primaryDocument": ["dks-20260825.htm", "dks-20260824.htm"],
                    "acceptanceDateTime": ["2026-08-25T07:01:00.000Z", "2026-08-24T17:00:00.000Z"],
                }
            }
        }
        self.primary_html = "<html><body><p>Item 2.02 Results of Operations and Financial Condition</p></body></html>"
        self.index_html = """
            <html><body><table class="tableFile">
              <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
              <tr><td>2</td><td>Quarterly earnings release</td>
                <td><a href="opaque-document.htm">opaque-document.htm</a></td><td>EX-99.1</td></tr>
            </table></body></html>
        """
        self.release_text = (
            "DICK'S SPORTING GOODS reports second quarter financial results and earnings. "
            + "Revenue and outlook information. " * 60
        )

    def test_discovers_exact_date_ex991_from_filing_index_even_with_opaque_filename(self) -> None:
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, self.index_html]
        ) as fetch_text, patch.object(
            self.provider, "_fetch_release_text", return_value=(self.release_text, "company_results")
        ):
            document = self.provider.discover("calendar:event-id")
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.event_id, "calendar:event-id")
        self.assertIn("opaque-document.htm", document.source_url)
        self.assertEqual(fetch_text.call_count, 2)

    def test_supports_exact_date_6k_earnings_filing(self) -> None:
        submissions = {
            "filings": {
                "recent": {
                    "form": ["6-K"],
                    "filingDate": ["2026-08-25"],
                    "accessionNumber": ["0001234567-26-000099"],
                    "primaryDocument": ["foreign-issuer-6k.htm"],
                    "acceptanceDateTime": ["2026-08-25T07:01:00.000Z"],
                }
            }
        }
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, submissions]), patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, self.index_html]
        ), patch.object(
            self.provider, "_fetch_release_text", return_value=(self.release_text, "company_results")
        ):
            document = self.provider.discover("calendar:event-id")
        self.assertIsNotNone(document)

    def test_loads_historical_submission_shard_when_target_is_not_recent(self) -> None:
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K"], "filingDate": ["2026-08-24"],
                    "accessionNumber": ["0001089063-26-000098"],
                    "primaryDocument": ["dks-20260824.htm"],
                    "acceptanceDateTime": ["2026-08-24T17:00:00.000Z"],
                },
                "files": [{
                    "name": "CIK0001089063-submissions-001.json",
                    "filingFrom": "2025-01-01", "filingTo": "2026-08-25",
                }],
            }
        }
        historical = {
            "form": ["8-K"], "filingDate": ["2026-08-25"],
            "accessionNumber": ["0001089063-26-000099"],
            "primaryDocument": ["dks-20260825.htm"],
            "acceptanceDateTime": ["2026-08-25T07:01:00.000Z"],
        }
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, submissions, historical]) as fetch_json, patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, self.index_html]
        ), patch.object(
            self.provider, "_fetch_release_text", return_value=(self.release_text, "company_results")
        ):
            document = self.provider.discover("calendar:event-id")
        self.assertIsNotNone(document)
        self.assertEqual(fetch_json.call_count, 3)

    def test_never_falls_back_to_neighboring_filing_date(self) -> None:
        submissions = {"filings": {"recent": {
            "form": ["8-K"], "filingDate": ["2026-08-24"],
            "accessionNumber": ["0001089063-26-000098"],
            "primaryDocument": ["dks-20260824.htm"],
            "acceptanceDateTime": ["2026-08-24T17:00:00.000Z"],
        }}}
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, submissions]), patch.object(
            self.provider, "_fetch_text"
        ) as fetch_text:
            document = self.provider.discover("calendar:event-id")
        self.assertIsNone(document)
        fetch_text.assert_not_called()

    def test_rejects_same_day_8k_without_earnings_signal_before_fetching_index(self) -> None:
        primary = "<html><body><p>Item 5.07 Submission of Matters to a Vote.</p></body></html>"
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", return_value=primary
        ) as fetch_text, patch.object(self.provider, "_fetch_release_text") as fetch_release:
            document = self.provider.discover("calendar:event-id")
        self.assertIsNone(document)
        self.assertEqual(fetch_text.call_count, 1)
        fetch_release.assert_not_called()

    def test_primary_sec_challenge_page_is_retryable_error(self) -> None:
        challenge = "<html><title>SEC.gov | Your Request Originates from an Undeclared Automated Tool</title></html>"
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", return_value=challenge
        ):
            with self.assertRaisesRegex(RuntimeError, "challenge/access page"):
                self.provider.discover("calendar:event-id")

    def test_type_must_come_from_authoritative_type_column(self) -> None:
        index = """
            <table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
            <tr><td>1</td><td>EX-99.1 earnings results</td><td><a href="earnings.htm">EX-99.1</a></td><td>8-K</td></tr></table>
        """
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, index]
        ), patch.object(self.provider, "_fetch_release_text") as fetch_release:
            document = self.provider.discover("calendar:event-id")
        self.assertIsNone(document)
        fetch_release.assert_not_called()

    def test_missing_document_type_headers_fail_closed(self) -> None:
        index = "<table><tr><th>Seq</th><th>File</th><th>Kind</th></tr></table>"
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, index]
        ):
            with self.assertRaisesRegex(RuntimeError, "DOCUMENT/TYPE layout"):
                self.provider.discover("calendar:event-id")

    def test_rejects_external_ex991_link_from_index(self) -> None:
        index = """
            <table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
            <tr><td>2</td><td>earnings</td><td><a href="https://example.com/x.htm">x</a></td><td>EX-99.1</td></tr></table>
        """
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, index]
        ), patch.object(self.provider, "_fetch_release_text") as fetch_release:
            document = self.provider.discover("calendar:event-id")
        self.assertIsNone(document)
        fetch_release.assert_not_called()

    def test_ex991_must_independently_look_like_results(self) -> None:
        unrelated = "Corporate governance exhibit. " * 50
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, self.index_html]
        ), patch.object(self.provider, "_fetch_release_text", return_value=(unrelated, "company_results")):
            self.assertIsNone(self.provider.discover("calendar:event-id"))

    def test_all_qualifying_ex991_retrieval_failures_are_errors(self) -> None:
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, self.index_html]
        ), patch.object(self.provider, "_fetch_release_text", side_effect=TimeoutError("SEC exhibit timeout")):
            with self.assertRaisesRegex(RuntimeError, "EX-99.1 retrieval failed"):
                self.provider.discover("calendar:event-id")

    def test_mixed_retrieved_and_failed_candidates_remain_retryable(self) -> None:
        index = """
            <table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
            <tr><td>2</td><td>first</td><td><a href="first.htm">first</a></td><td>EX-99.1</td></tr>
            <tr><td>3</td><td>second</td><td><a href="second.htm">second</a></td><td>EX-99.1</td></tr></table>
        """
        unrelated = "Corporate governance exhibit. " * 50
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, index]
        ), patch.object(self.provider, "_fetch_release_text", side_effect=[TimeoutError("first failed"), (unrelated, "company_results")]):
            with self.assertRaisesRegex(RuntimeError, "EX-99.1 retrieval failed"):
                self.provider.discover("calendar:event-id")

    def test_pdf_extraction_failure_is_retried_as_retrieval_error(self) -> None:
        pdf_index = self.index_html.replace("opaque-document.htm", "opaque-document.pdf")
        with patch.object(self.provider, "_fetch_json", side_effect=[self.tickers, self.submissions]), patch.object(
            self.provider, "_fetch_text", side_effect=[self.primary_html, pdf_index]
        ), patch.object(self.provider, "_fetch_bytes", return_value=(b"bad", "application/pdf", "utf-8")), patch(
            "trading_system.sec_release_ingestion.PdfReader", side_effect=RuntimeError("encrypted PDF")
        ):
            with self.assertRaisesRegex(RuntimeError, "EX-99.1 retrieval failed"):
                self.provider.discover("calendar:event-id")

    def test_misaligned_required_filing_arrays_fail_closed(self) -> None:
        malformed = {
            "form": ["8-K", "8-K"], "filingDate": ["2026-08-24", "2026-08-25"],
            "accessionNumber": ["0001089063-26-000098"], "primaryDocument": ["old.htm", "target.htm"],
        }
        with self.assertRaisesRegex(RuntimeError, "required arrays are misaligned"):
            self.provider._matching_filings_table(malformed)

    def test_matching_filing_with_empty_identifier_fails_closed(self) -> None:
        malformed = {
            "form": ["8-K"],
            "filingDate": ["2026-08-25"],
            "accessionNumber": [""],
            "primaryDocument": ["target.htm"],
        }
        with self.assertRaisesRegex(RuntimeError, "empty accessionNumber or primaryDocument"):
            self.provider._matching_filings_table(malformed)

    def test_malformed_historical_metadata_fails_closed(self) -> None:
        payload = {"filings": {"files": [{"name": "CIK0001089063-submissions-001.json", "filingFrom": "2025-01-01"}]}}
        with self.assertRaisesRegex(RuntimeError, "metadata is incomplete"):
            self.provider._matching_historical_filings(payload)

    def test_historical_metadata_invalid_or_reversed_dates_fail_closed(self) -> None:
        invalid = {"filings": {"files": [{
            "name": "CIK0001089063-submissions-001.json",
            "filingFrom": "not-a-date",
            "filingTo": "2026-08-25",
        }]}}
        with self.assertRaisesRegex(RuntimeError, "invalid date range"):
            self.provider._matching_historical_filings(invalid)

        reversed_range = {"filings": {"files": [{
            "name": "CIK0001089063-submissions-001.json",
            "filingFrom": "2026-08-26",
            "filingTo": "2026-08-25",
        }]}}
        with self.assertRaisesRegex(RuntimeError, "reversed date range"):
            self.provider._matching_historical_filings(reversed_range)

    def test_company_ticker_map_is_reused_across_providers(self) -> None:
        other = SecEdgarResultsProvider(ticker="DKS", scheduled_date=date(2026, 8, 25), user_agent="MarketAI test@example.invalid")
        with patch.object(self.provider, "_fetch_json", return_value=self.tickers) as first_fetch:
            self.assertEqual(self.provider._resolve_cik(), 1089063)
        with patch.object(other, "_fetch_json") as second_fetch:
            self.assertEqual(other._resolve_cik(), 1089063)
        first_fetch.assert_called_once_with(self.provider.COMPANY_TICKERS_URL)
        second_fetch.assert_not_called()

    def test_sec_user_agent_is_required_and_must_include_contact_email(self) -> None:
        with patch.dict(os.environ, {"MARKETAI_SEC_USER_AGENT": ""}, clear=False):
            with self.assertRaisesRegex(ValueError, "MARKETAI_SEC_USER_AGENT is required"):
                SecEdgarResultsProvider(ticker="DKS", scheduled_date=date(2026, 8, 25))
        with self.assertRaisesRegex(ValueError, "contact email address"):
            SecEdgarResultsProvider(ticker="DKS", scheduled_date=date(2026, 8, 25), user_agent="MarketAI")

    def test_ticker_must_resolve_to_exactly_one_cik(self) -> None:
        ambiguous = {"0": {"cik_str": 1, "ticker": "DKS"}, "1": {"cik_str": 2, "ticker": "DKS"}}
        with patch.object(self.provider, "_fetch_json", return_value=ambiguous):
            with self.assertRaisesRegex(RuntimeError, "exactly one CIK"):
                self.provider.discover("calendar:event-id")


if __name__ == "__main__":
    unittest.main()
