from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


class ResultsPageReleaseParserScopeRegressionTests(unittest.TestCase):
    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results",
            version=1,
        )

    def test_explicit_list_item_close_respects_list_item_scope_boundary(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<ul><li hidden>old<ul></li><a href="/r">Q2-2026</a></ul></li></ul>',
        )
        self.assertEqual(candidates, ())

    def test_new_anchor_start_finalizes_active_anchor(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Q2-2026<a href="/other">Annual</a>',
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))
        self.assertEqual(candidates[1].source_url, "https://investor.example.com/other")
        self.assertEqual(candidates[1].evidence_fields, ("Annual",))

    def test_heading_start_implicitly_closes_open_heading(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<h1 hidden>old<h2><a href="/r">Q2-2026</a></h2>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))


if __name__ == "__main__":
    unittest.main()
