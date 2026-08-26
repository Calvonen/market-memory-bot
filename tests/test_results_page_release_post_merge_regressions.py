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

    def _page_source(self, source_url: str) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url=source_url,
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

    def test_deduplication_aggregates_unique_evidence_across_many_duplicates(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Q2-2026</a>'
            '<a href="/release.pdf?">Interim report</a>'
            '<a href="/release.pdf#again">Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_title, "Q2-2026")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026", "Interim report"))

    def test_deduplication_takes_first_non_empty_title(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf"></a><a href="/release.pdf#named">Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_title, "Q2-2026")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_terminal_parent_dot_segment_removes_previous_segment(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/a/b/..">release</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/a/")

    def test_terminal_parent_dot_segment_cannot_escape_the_authority_root(self):
        candidates = extract_results_page_candidates(
            self._page_source("https://investor.example.com/results"),
            '<a href="/..">root</a><a href="/a/../..">still root</a>',
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            ["https://investor.example.com/"],
        )

    def test_relative_terminal_parent_dot_segment_removes_previous_segment(self):
        candidates = extract_results_page_candidates(
            self._page_source("https://investor.example.com/results/reports/index.html"),
            '<a href="..">parent</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/results/")

    def test_rendered_data_element_is_discoverable(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<data value="period"><a href="/release.pdf">Q2-2026</a></data>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_rendered_data_element_supplies_inline_evidence(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Interim <data value="2026-Q2">Q2-2026</data> report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].evidence_fields, ("Interim Q2-2026 report",))

    def test_hidden_data_element_still_suppresses_its_anchor(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<data hidden><a href="/hidden.pdf">Q2-2026</a></data>'
            '<a href="/release.pdf">Annual report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")

    def test_html_anchor_inside_svg_integration_point_is_discoverable(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Q2-2026<svg><foreignObject>'
            '<a href="/other">Annual</a></foreignObject></svg>',
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/q",
                "https://investor.example.com/other",
            ],
        )
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))
        self.assertEqual(candidates[1].evidence_fields, ("Annual",))

    def test_foreign_namespace_anchors_are_still_not_release_candidates(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<svg><a href="/other">icon</a></svg></a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")

    def test_hidden_ancestor_still_suppresses_integration_point_anchors(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<div hidden><svg><foreignObject>'
            '<a href="/hidden.pdf">Q2-2026</a></foreignObject></svg></div>'
            '<a href="/release.pdf">Annual report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")

    def test_template_local_anchor_never_becomes_a_release_candidate(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<template><a href="/other">hidden</a></template></a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")

    def test_template_local_anchor_is_suppressed_inside_an_integration_point(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<template><svg><foreignObject>'
            '<a href="/other">Q2-2026</a></foreignObject></svg></template>'
            '<a href="/release.pdf">Annual report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")

    def test_template_suppression_keeps_an_href_that_is_also_rendered(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Q2-2026</a>'
            '<template><a href="/release.pdf">template copy</a></template>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_svg_template_is_not_html_template_suppression(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<svg><template><div><a href="/r">Q2-2026</a></div></template></svg>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_template_suppression_keeps_same_origin_and_raw_href_guards(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<template><a href="/inside.pdf">hidden</a></template>'
            '<a href="http://investor.example.com/insecure.pdf">insecure</a>'
            '<a href="https://other.example.com/cross.pdf">cross origin</a>'
            '<a href="/release.pdf">Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")


if __name__ == "__main__":
    unittest.main()
