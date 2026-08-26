from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


class ResultsPageReleaseParserFinalEdgeRegressionTests(unittest.TestCase):
    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results",
            version=1,
        )

    def test_html_end_br_creates_rendered_break(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/r">Q2</br>-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2 -2026",))

    def test_href_less_hidden_anchor_is_closed_before_visible_nested_anchor(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a hidden>old<a href="/r">Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_legacy_center_start_closes_hidden_paragraph(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<p hidden>old<center><a href="/r">Q2-2026</a></center>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))


if __name__ == "__main__":
    unittest.main()
