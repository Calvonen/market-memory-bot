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

    def test_rejects_trailing_dot_and_double_dot_host_spellings(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="https://investor.example.com./release.pdf">trailing dot</a>
            <a href="https://investor.example.com../release.pdf">double dot</a>
            <a href="/valid.pdf">valid</a>
            """,
        )

        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            ["https://investor.example.com/valid.pdf"],
        )

    def test_malformed_ipv6_candidate_does_not_abort_later_valid_links(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="https://[invalid/release.pdf">bad ipv6</a>
            <a href="/valid.pdf">valid</a>
            """,
        )

        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            ["https://investor.example.com/valid.pdf"],
        )

    def test_rejects_raw_control_characters_before_url_parsing(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="https://investor.examp&#10;le.com/release.pdf">newline</a>
            <a href="https://investor.examp&#13;le.com/release2.pdf">carriage return</a>
            <a href="https://investor.examp&#9;le.com/release3.pdf">tab</a>
            <a href="/valid.pdf">valid</a>
            """,
        )

        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            ["https://investor.example.com/valid.pdf"],
        )

    def test_rejects_control_characters_before_stripping_href(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="https://investor.example.com/release.pdf&#10;">trailing newline</a>
            <a href="&#9;/release2.pdf">leading tab</a>
            <a href="/valid.pdf">valid</a>
            """,
        )

        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            ["https://investor.example.com/valid.pdf"],
        )

    def test_canonicalizes_authority_before_deduplicating(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="https://INVESTOR.EXAMPLE.COM/release.pdf">first</a>
            <a href="https://investor.example.com:443/release.pdf">second</a>
            <a href="/release.pdf">third</a>
            """,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].source_url,
            "https://investor.example.com/release.pdf",
        )
        self.assertEqual(candidates[0].source_title, "first")

    def test_normalizes_dot_segments_before_self_page_and_deduplication(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="https://investor.example.com/archive/../results">same page</a>
            <a href="https://investor.example.com/archive/../release.pdf">first</a>
            <a href="/release.pdf">duplicate</a>
            """,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].source_url,
            "https://investor.example.com/release.pdf",
        )
        self.assertEqual(candidates[0].source_title, "first")

    def test_normalizes_empty_https_path_to_slash_for_self_page_and_deduplication(self):
        source = OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com",
            source_title="Results",
            version=1,
        )

        candidates = extract_results_page_candidates(
            source,
            """
            <a href="/">same page slash</a>
            <a href="https://investor.example.com">same page empty path</a>
            <a href="/release.pdf">release</a>
            """,
        )

        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            ["https://investor.example.com/release.pdf"],
        )

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
