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

    def test_rejects_raw_and_encoded_ascii_controls_before_html_decoding(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="https://investor.examp&#10;le.com/release.pdf">newline</a>
            <a href="https://investor.example.com/release2.pdf&#12;">form feed</a>
            <a href="&#31;/release3.pdf">unit separator</a>
            <a href="&#127;/release4.pdf">del</a>
            <a href="&Tab;/release5.pdf">named tab</a>
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
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].source_title, "first")

    def test_rfc_dot_segment_removal_preserves_slashes_and_trailing_semantics(self):
        source = OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results/",
            source_title="Results",
            version=1,
        )
        candidates = extract_results_page_candidates(
            source,
            """
            <a href="https://investor.example.com/results/.">same page dot</a>
            <a href="https://investor.example.com/results/child/..">same page parent</a>
            <a href="https://investor.example.com/a//b">double slash resource</a>
            <a href="https://investor.example.com/a/./c/../d">dot normalized</a>
            """,
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/a//b",
                "https://investor.example.com/a/d",
            ],
        )

    def test_relative_resolution_preserves_empty_segments(self):
        source = OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results/page",
            source_title="Results",
            version=1,
        )
        candidates = extract_results_page_candidates(
            source,
            '<a href="a//b">release</a>',
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            ["https://investor.example.com/results/a//b"],
        )

    def test_semicolon_parameters_remain_part_of_rfc_path_segments(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <a href="https://investor.example.com/a/.;p">dot-like param</a>
            <a href="https://investor.example.com/a/..;p">parent-like param</a>
            """,
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/a/.;p",
                "https://investor.example.com/a/..;p",
            ],
        )

    def test_invalid_approved_base_url_fails_closed_before_relative_resolution(self):
        source = OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results\x7f",
            source_title="Results",
            version=1,
        )
        candidates = extract_results_page_candidates(source, '<a href="/release.pdf">release</a>')
        self.assertEqual(candidates, ())

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
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
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
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
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
