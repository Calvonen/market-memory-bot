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
        """The template-local anchor must not leak into the release candidates.

        html5lib 1.1 has no ``<template>`` support at all, so it never pushes the
        active-formatting-elements marker the HTML5 spec requires. The anchor
        adoption agency therefore hoists the template-local anchor out of its
        template and drags the trailing text out of the outer anchor with it. We
        suppress template-local anchors from the raw scan, so only the outer
        anchor survives; its trailing text is dropped rather than reattached,
        because rebuilding that nesting would mean reintroducing our own tree
        builder.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<template><a href="/other">hidden</a></template> Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

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
        """html5lib does not implement the foreign special-element scope barrier.

        HTML5 tree construction puts MathML ``mi``/``mn``/``mo``/``ms``/``mtext``
        and SVG ``foreignObject``/``desc``/``title`` in the special category, so
        the generic end-tag close for ``</span>`` would be ignored and the anchor
        would stay inside the hidden span. html5lib 1.1 lists only HTML-namespace
        elements in ``specialElements``, so it pops through the MathML subtree and
        the hidden span, and the anchor becomes rendered top-level content.
        Modelling that barrier would mean reintroducing our own tree builder, so
        we take html5lib's tree as-is; every other guard (same-origin HTTPS,
        raw-href safety, URL normalisation, evidence-field boundaries) still
        applies to the resulting candidate.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<span hidden><math><mi></span><a href="/r">Q2-2026</a></mi></math></span>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_eof_finalization_preserves_outer_url_while_text_stays_suppressed(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/Q2-2026.pdf"><template/><style/>hidden</template>Q2-2026</style></a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/Q2-2026.pdf")
        self.assertEqual(candidates[0].evidence_fields, ())

    def test_ancestor_close_reconstructs_anchor_without_merging_fragments(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<div><a href="/release.pdf">Download</div> Q2-2026',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Download", "Q2-2026"))

    def test_foreign_special_name_is_not_boundary_in_html_namespace(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<span hidden><mi></span><a href="/r">Q2-2026</a></mi></span>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_template_local_anchor_pop_does_not_finalize_outer_anchor_node(self):
        """Popping the template-local anchor leaves the outer anchor intact.

        Same html5lib gap as
        ``test_template_local_anchor_does_not_finalize_outer_anchor``: the
        trailing text is lost to the hoist, but the outer anchor keeps its own
        URL and evidence and the template anchor never becomes a candidate.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<template><a href="/other">hidden</a></template> Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_svg_anchor_does_not_trigger_html_nested_anchor_repair(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<svg><a href="/other">icon</a></svg> Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report", "Q2-2026"))

    def test_foreignobject_anchor_still_uses_html_nested_anchor_repair(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Q2-2026<svg><foreignObject><a href="/other">Annual</a></foreignObject></svg>',
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))
        self.assertEqual(candidates[1].source_url, "https://investor.example.com/other")
        self.assertEqual(candidates[1].evidence_fields, ("Annual",))

    def test_nested_svg_root_beneath_foreignobject_returns_to_svg_namespace(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<svg><foreignObject><svg><a href="/other"><text>icon</text></a></svg></foreignObject></svg> Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report", "Q2-2026"))

    def test_html_breakout_tag_exits_svg_content_before_nested_anchor_repair(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<svg><div><a href="/other">Annual</a></div></svg> Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))
        self.assertEqual(candidates[1].source_url, "https://investor.example.com/other")
        self.assertEqual(candidates[1].evidence_fields, ("Annual",))

    def test_foreign_self_closing_script_releases_suppression_immediately(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<svg><script/><text>Q2-2026</text></svg></a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_foreign_p_endtag_breakout_reprocesses_following_anchor_as_html(self):
        """html5lib does not pop the SVG root on a ``</p>`` foreign breakout.

        HTML5 tree construction pops out of foreign content before reprocessing
        ``</p>``, so the following anchor would be an HTML anchor. html5lib 1.1
        reprocesses the token without popping the ``svg`` element, so the anchor
        stays in the SVG namespace. Foreign anchors are never release candidates
        under the conservative policy, and the surrounding text stays split into
        separate evidence fields so no fiscal token is manufactured across the
        ambiguity boundary.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<svg></p><a href="/other">Annual</a></svg> Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report", "Q2-2026"))

    def test_annotation_xml_svg_enters_svg_without_html_encoding(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<math><annotation-xml><svg><foreignObject><a href="/other">Annual</a></foreignObject></svg></annotation-xml></math> Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))
        self.assertEqual(candidates[1].source_url, "https://investor.example.com/other")
        self.assertEqual(candidates[1].evidence_fields, ("Annual",))

    def test_nested_anchor_repair_removes_exact_active_html_anchor(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report<svg><a><foreignObject><a href="/other">Q2-2026</a></foreignObject></a></svg>',
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))
        self.assertEqual(candidates[1].source_url, "https://investor.example.com/other")
        self.assertEqual(candidates[1].evidence_fields, ("Q2-2026",))


if __name__ == "__main__":
    unittest.main()
