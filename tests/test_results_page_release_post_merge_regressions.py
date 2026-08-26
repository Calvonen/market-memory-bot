from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


class ResultsPageReleasePostMergeRegressionTests(unittest.TestCase):
    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results/index.html",
            version=1,
        )

    def test_deduplication_preserves_first_display_title(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">first</a><a href="/release.pdf#duplicate">second</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_title, "first")
        self.assertEqual(candidates[0].evidence_fields, ("first", "second"))

    def test_terminal_parent_dot_segment_removes_previous_segment(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/a/b/..">release</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/a/")

    def test_rendered_data_element_is_discoverable(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<data value="period"><a href="/release.pdf">Q2-2026</a></data>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))


if __name__ == "__main__":
    unittest.main()
