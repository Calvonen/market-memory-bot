from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from trading_system.sec_release_ingestion import SecEdgarResultsProvider


class SecEdgarResultsProviderTests(unittest.TestCase):
    def setUp(self) -> None:
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
        self.primary_html = """
            <html><body>
            <p>Item 2.02 Results of Operations and Financial Condition</p>
            </body></html>
        """
        self.index_html = """
            <html><body><table class="tableFile">
              <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
              <tr>
                <td>2</td><td>Quarterly earnings release</td>
                <td><a href="opaque-document.htm">opaque-document.htm</a></td>
                <td>EX-99.1</td>
              </tr>
            </table></body></html>
        """
        self.release_text = (
            "DICK'S SPORTING GOODS reports second quarter financial results and earnings. "
            + "Revenue and outlook information. " * 60
        )

    def test_discovers_exact_date_ex991_from_filing_index_even_with_opaque_filename(self) -> None:
        with patch.object(
            self.provider,
            "_fetch_json",
            side_effect=[self.tickers, self.submissions],
        ), patch.object(
            self.provider,
            "_fetch_text",
            side_effect=[self.primary_html, self.index_html],
        ) as fetch_text, patch.object(
            self.provider,
            "_fetch_release_text",
            return_value=(self.release_text, "company_results"),
        ):
            document = self.provider.discover("calendar:event-id")

        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.event_id, "calendar:event-id")
        self.assertEqual(document.source_type, "company_results")
        self.assertIn("opaque-document.htm", document.source_url)
        self.assertTrue(document.source_title.startswith("EX-99.1"))
        self.assertEqual(fetch_text.call_count, 2)
        primary_url = fetch_text.call_args_list[0].args[0]
        index_url = fetch_text.call_args_list[1].args[0]
        self.assertIn("000108906326000099", primary_url)
        self.assertIn("0001089063-26-000099-index.html", index_url)
        self.assertNotIn("000108906326000098", primary_url)
        self.assertNotIn("000108906326000098", index_url)

    def test_never_falls_back_to_neighboring_filing_date(self) -> None:
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": ["2026-08-24"],
                    "accessionNumber": ["0001089063-26-000098"],
                    "primaryDocument": ["dks-20260824.htm"],
                    "acceptanceDateTime": ["2026-08-24T17:00:00.000Z"],
                }
            }
        }
        with patch.object(
            self.provider,
            "_fetch_json",
            side_effect=[self.tickers, submissions],
        ), patch.object(self.provider, "_fetch_text") as fetch_text:
            document = self.provider.discover("calendar:event-id")

        self.assertIsNone(document)
        fetch_text.assert_not_called()

    def test_rejects_same_day_8k_without_earnings_signal_before_fetching_index(self) -> None:
        primary = "<html><body><p>Item 5.07 Submission of Matters to a Vote.</p></body></html>"
        with patch.object(
            self.provider,
            "_fetch_json",
            side_effect=[self.tickers, self.submissions],
        ), patch.object(self.provider, "_fetch_text", return_value=primary) as fetch_text, patch.object(
            self.provider, "_fetch_release_text"
        ) as fetch_release:
            document = self.provider.discover("calendar:event-id")

        self.assertIsNone(document)
        self.assertEqual(fetch_text.call_count, 1)
        fetch_release.assert_not_called()

    def test_rejects_misleading_earnings_link_when_row_type_is_not_ex991(self) -> None:
        index = """
            <html><body><table>
              <tr><td>1</td><td>earnings results</td>
                  <td><a href="earnings-results.htm">earnings results</a></td>
                  <td>8-K</td></tr>
              <tr><td>2</td><td>press release</td>
                  <td><a href="other.htm">other.htm</a></td>
                  <td>EX-99.2</td></tr>
            </table></body></html>
        """
        with patch.object(
            self.provider,
            "_fetch_json",
            side_effect=[self.tickers, self.submissions],
        ), patch.object(
            self.provider,
            "_fetch_text",
            side_effect=[self.primary_html, index],
        ), patch.object(self.provider, "_fetch_release_text") as fetch_release:
            document = self.provider.discover("calendar:event-id")

        self.assertIsNone(document)
        fetch_release.assert_not_called()

    def test_rejects_external_ex991_link_from_index(self) -> None:
        index = """
            <html><body><table>
              <tr><td>2</td><td>earnings release</td>
                  <td><a href="https://example.com/opaque.htm">opaque.htm</a></td>
                  <td>EX-99.1</td></tr>
            </table></body></html>
        """
        with patch.object(
            self.provider,
            "_fetch_json",
            side_effect=[self.tickers, self.submissions],
        ), patch.object(
            self.provider,
            "_fetch_text",
            side_effect=[self.primary_html, index],
        ), patch.object(self.provider, "_fetch_release_text") as fetch_release:
            document = self.provider.discover("calendar:event-id")

        self.assertIsNone(document)
        fetch_release.assert_not_called()

    def test_ex991_must_independently_look_like_results(self) -> None:
        unrelated = "Corporate governance exhibit. " * 50
        with patch.object(
            self.provider,
            "_fetch_json",
            side_effect=[self.tickers, self.submissions],
        ), patch.object(
            self.provider,
            "_fetch_text",
            side_effect=[self.primary_html, self.index_html],
        ), patch.object(
            self.provider,
            "_fetch_release_text",
            return_value=(unrelated, "company_results"),
        ):
            document = self.provider.discover("calendar:event-id")

        self.assertIsNone(document)

    def test_ticker_must_resolve_to_exactly_one_cik(self) -> None:
        ambiguous = {
            "0": {"cik_str": 1, "ticker": "DKS"},
            "1": {"cik_str": 2, "ticker": "DKS"},
        }
        with patch.object(self.provider, "_fetch_json", return_value=ambiguous):
            with self.assertRaisesRegex(RuntimeError, "exactly one CIK"):
                self.provider.discover("calendar:event-id")


if __name__ == "__main__":
    unittest.main()
