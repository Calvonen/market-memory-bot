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

    def test_generic_explicit_close_respects_base_scope_boundary(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<div hidden><table></div><a href="/r">Q2-2026</a></table></div>',
        )
        self.assertEqual(candidates, ())

    def test_template_close_checks_scope_before_releasing_suppression(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<template><table></template><a href="/r">Q2-2026</a></table></template>',
        )
        self.assertEqual(candidates, ())

    def test_template_local_anchor_does_not_finalize_outer_anchor(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<template><a href="/other">hidden</a></template> Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report Q2-2026",))

    def test_paragraph_and_heading_implied_closes_run_sequentially(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<h1 hidden><p>old<h2><a href="/r">Q2-2026</a></h2>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_generic_explicit_close_stops_at_special_element(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<span hidden><ul></span><a href="/r">Q2-2026</a></ul></span>',
        )
        self.assertEqual(candidates, ())

    def test_heading_start_only_closes_current_heading_node(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<h1 hidden><div>old<h2><a href="/r">Q2-2026</a></h2></div></h1>',
        )
        self.assertEqual(candidates, ())

    def test_generic_explicit_close_stops_at_foreign_special_element(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<span hidden><math><mi></span><a href="/r">Q2-2026</a></mi></math></span>',
        )
        self.assertEqual(candidates, ())

    def test_eof_finalization_preserves_outer_url_while_text_stays_suppressed(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/Q2-2026.pdf"><template/><style/>hidden</template>Q2-2026</style></a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/Q2-2026.pdf")
        self.assertEqual(candidates[0].evidence_fields, ())

    def test_ancestor_close_finalizes_anchor_before_outside_text(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<div><a href="/release.pdf">Download</div> Q2-2026',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Download",))

    def test_foreign_special_name_is_not_boundary_in_html_namespace(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<span hidden><mi></span><a href="/r">Q2-2026</a></mi></span>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))


if __name__ == "__main__":
    unittest.main()
