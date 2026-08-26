from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


class ResultsPageReleaseCandidateTests(unittest.TestCase):
    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results",
            source_title="Results",
            version=1,
        )

    def test_extracts_relative_and_same_origin_https_links(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <html><body>
              <a href="/reports/q2-2026.pdf">Q2 2026 results</a>
              <a href="https://investor.example.com/releases/h1-2026">Half-year results</a>
            </body></html>
            """,
        )

        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/reports/q2-2026.pdf",
                "https://investor.example.com/releases/h1-2026",
            ],
        )
        self.assertEqual(candidates[0].event_id, "calendar:test-event")
        self.assertEqual(candidates[0].source_title, "Q2 2026 results")

    def test_rejects_cross_origin_non_https_and_credentials(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="https://evil.example/release.pdf">wrong host</a>
            <a href="http://investor.example.com/release.pdf">http</a>
            <a href="https://user:pass@investor.example.com/release.pdf">credentials</a>
            <a href="mailto:ir@example.com">mail</a>
            <a href="javascript:alert(1)">script</a>
            """,
        )

        self.assertEqual(candidates, ())

    def test_deduplicates_fragments_and_ignores_results_page_itself(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="/results#top">same page</a>
            <a href="/release.pdf#download">PDF</a>
            <a href="https://investor.example.com/release.pdf">PDF duplicate</a>
            """,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].source_url,
            "https://investor.example.com/release.pdf",
        )
        self.assertEqual(candidates[0].source_title, "PDF")

    def test_preserves_accessible_link_title_when_anchor_text_is_empty(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf" aria-label="Q2 results"></a>',
        )

        self.assertEqual(candidates[0].source_title, "Q2 results")

    def test_direct_url_source_is_rejected(self):
        source = OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="direct_url",
            source_url="https://investor.example.com/release.pdf",
            version=1,
        )

        with self.assertRaisesRegex(ValueError, "source_kind=results_page"):
            extract_results_page_candidates(source, "<html></html>")


if __name__ == "__main__":
    unittest.main()
