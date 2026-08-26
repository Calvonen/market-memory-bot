from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import (
    _RawAnchorHrefSafetyScanner,
    extract_results_page_candidates,
)


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

    def test_template_local_anchor_sharing_a_rendered_href_is_suppressed(self):
        """A rendered spelling must not unsuppress the template spelling.

        html5lib hoists the template-local anchor out of its template, so it
        reaches the tree as a duplicate of the rendered candidate. Suppression is
        per occurrence, so the rendered anchor stays a candidate and the template
        content contributes no evidence to it.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Annual</a>'
            '<a href="/q">'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</a>',
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/release.pdf",
                "https://investor.example.com/q",
            ],
        )
        self.assertEqual(candidates[0].source_title, "Annual")
        self.assertEqual(candidates[0].evidence_fields, ("Annual",))
        self.assertEqual(candidates[1].evidence_fields, ())

    def test_template_local_anchor_before_its_rendered_twin_is_suppressed(self):
        """Occurrence matching follows document order, whichever spelling is first."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</a>'
            '<a href="/release.pdf">Annual</a>',
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/q",
                "https://investor.example.com/release.pdf",
            ],
        )
        self.assertEqual(candidates[1].source_title, "Annual")
        self.assertEqual(candidates[1].evidence_fields, ("Annual",))

    def test_foreign_anchor_does_not_consume_a_template_occurrence_index(self):
        """An SVG anchor must not shift the occurrence index of the HTML anchors.

        The raw scanner sees three spellings of /release.pdf, but only two of them
        become HTML anchors in the tree, so matching HTML anchors alone against the
        raw list would align the hoisted template anchor with the SVG anchor's
        non-template occurrence and let its hidden evidence through.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Annual</a>'
            '<svg><a href="/release.pdf">icon</a></svg>'
            '<a href="/q">'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</a>',
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/release.pdf",
                "https://investor.example.com/q",
            ],
        )
        self.assertEqual(candidates[0].source_title, "Annual")
        self.assertEqual(candidates[0].evidence_fields, ("Annual",))
        self.assertNotIn("Q2-2026", candidates[0].evidence_fields)
        self.assertEqual(candidates[1].evidence_fields, ())

    def test_mathml_anchor_does_not_consume_a_template_occurrence_index(self):
        """MathML anchors hold their own occurrence slot just like SVG ones."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Annual</a>'
            '<math><a href="/release.pdf">glyph</a></math>'
            '<a href="/q">'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</a>',
        )
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual",))

    def test_integration_point_anchor_keeps_its_own_occurrence_slot(self):
        """An anchor html5lib promotes to HTML still matches its own spelling.

        Inside foreignObject the anchor becomes an HTML anchor, so it must keep
        the rendered slot and the template spelling must stay suppressed.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<svg><foreignObject>'
            '<a href="/release.pdf">Annual</a>'
            '</foreignObject></svg>'
            '<template><a href="/release.pdf">Q2-2026</a></template>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual",))

    def test_unprovable_anchor_correspondence_fails_closed(self):
        """A dropped anchor makes the occurrence match unprovable, so the href goes.

        html5lib discards the anchor inside <select>, so the raw occurrences and
        the tree anchors no longer describe the same anchors. Rather than risk
        matching the template spelling against a rendered slot, every candidate
        for that href is dropped.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<select><a href="/release.pdf">dropped</a></select>'
            '<a href="/release.pdf">Annual</a>'
            '<a href="/q">'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ())

    def test_dropped_anchor_without_a_template_keeps_its_candidate(self):
        """The fail-closed path only applies to hrefs a template also spells."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<select><a href="/release.pdf">dropped</a></select>'
            '<a href="/release.pdf">Annual</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual",))

    def test_only_the_template_occurrence_of_a_repeated_href_is_suppressed(self):
        """Occurrence matching picks out the template slot among several duplicates."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Annual</a>'
            '<a href="/q">'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</a>'
            '<a href="/release.pdf">Interim</a>',
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/release.pdf",
                "https://investor.example.com/q",
            ],
        )
        self.assertEqual(candidates[0].source_title, "Annual")
        self.assertEqual(candidates[0].evidence_fields, ("Annual", "Interim"))

    def test_non_hoisted_template_anchor_keeps_its_rendered_twin_intact(self):
        """A template anchor that stays inside its template still holds its slot."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '<a href="/release.pdf">Annual</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual",))

    def test_self_closing_template_opens_a_template_scope(self):
        """<template/> is a plain start tag, not a self-closing void element.

        HTML has no self-closing syntax for ordinary elements, so the anchor that
        follows is template content until the matching </template>.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">'
            '<template/>'
            '<a href="/hidden.pdf">Q2-2026</a>'
            '</template>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ())

    def test_self_closing_template_scope_ends_at_its_end_tag(self):
        """The <template/> scope closes at </template>, not before and not after."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<template/><a href="/hidden.pdf">Q2-2026</a></template>'
            '<a href="/release.pdf">Annual report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual report",))

    def test_self_closing_svg_does_not_open_a_foreign_scope(self):
        """A foreign element does acknowledge its self-closing flag.

        <svg/> opens and closes in one step, so the <template> after it is an
        ordinary HTML template and still suppresses its anchor.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<svg/><template><a href="/hidden.pdf">Q2-2026</a></template>'
            '<a href="/release.pdf">Annual report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")

    def test_template_inside_svg_integration_point_is_html_template(self):
        """foreignObject hands content back to HTML, so <template> there suppresses.

        Foreign depth alone is too coarse a test for template semantics: an HTML
        integration point restores HTML tree construction while the SVG root is
        still open.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">'
            '<svg><foreignObject>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</foreignObject></svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ())

    def test_self_closing_template_inside_svg_integration_point_is_html_template(self):
        """The <template/> spelling opens the same scope inside foreignObject."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">'
            '<svg><foreignObject>'
            '<template/><a href="/release.pdf">Q2-2026</a></template>'
            '</foreignObject></svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ())

    def test_hoisted_integration_point_template_anchor_does_not_leak(self):
        """The same template, in the shape where html5lib actually hoists it out."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg><foreignObject>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</foreignObject></svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_template_after_a_foreign_breakout_tag_is_html_template(self):
        """A breakout start tag pops out of foreign content before the template."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg><div>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</div></svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_template_inside_mathml_integration_point_is_html_template(self):
        """MathML text integration points restore HTML tree construction too."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<math><mtext>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</mtext></math>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_template_inside_svg_title_is_html_template(self):
        """<svg><title> holds markup, not the text-only content HTML gives <title>.

        The raw scanner must not drop into text-only mode inside foreign content,
        or the anchor goes unrecorded and never reaches the occurrence match.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg><title>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</title></svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_integration_point_template_does_not_suppress_a_rendered_twin(self):
        """Only the template occurrence goes, even inside an integration point."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Annual</a>'
            '<a href="/q">Report'
            '<svg><foreignObject>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</foreignObject></svg>'
            '</a>',
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/release.pdf",
                "https://investor.example.com/q",
            ],
        )
        self.assertEqual(candidates[0].evidence_fields, ("Annual",))
        self.assertNotIn("Q2-2026", candidates[0].evidence_fields)

    def test_self_closing_breakout_tag_still_breaks_out_of_foreign_content(self):
        """<div/> is an HTML element, so the slash is ignored and it still breaks out.

        Only foreign elements acknowledge the self-closing flag. A self-closing
        breakout tag therefore leaves foreign content exactly like the ordinary
        spelling, and the <template> after it is a real HTML template.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg><div/>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_self_closing_breakout_variants_all_break_out_of_foreign_content(self):
        """The rule is the breakout list, not a special case for <div/>."""

        for tag in ("span", "b", "br", "hr", "img", "table", "p", "font"):
            with self.subTest(tag=tag):
                candidates = extract_results_page_candidates(
                    self._source(),
                    '<a href="/q">Report'
                    f'<svg><{tag}/>'
                    '<template><a href="/release.pdf">Q2-2026</a></template>'
                    '</svg>'
                    '</a>',
                )
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
                self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_self_closing_foreign_element_still_balances_its_scope(self):
        """A foreign element does acknowledge the flag, so <foreignObject/> closes.

        The template that follows is back in bare SVG content, where <template>
        has no HTML template semantics, so its anchor is not suppressed as
        template content.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<svg><foreignObject/>'
            '<template><div><a href="/release.pdf">Q2-2026</a></div></template>'
            '</svg>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_unmatched_integration_point_end_tag_keeps_the_scope_open(self):
        """A stray </title> inside foreignObject does not close the integration point.

        HTML5 walks the open elements for a match and ignores an end tag naming
        something else, so the region stays HTML content and the <template> after
        it still suppresses its anchor.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg><foreignObject></title>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</foreignObject></svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_unmatched_integration_point_end_tag_does_not_leak_to_a_visible_twin(self):
        """The shape where the stray end tag actually let hidden evidence through."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Annual</a>'
            '<a href="/q">Report'
            '<svg><desc></title>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</desc></svg>'
            '</a>',
        )
        self.assertEqual(
            [candidate.source_url for candidate in candidates],
            [
                "https://investor.example.com/release.pdf",
                "https://investor.example.com/q",
            ],
        )
        self.assertEqual(candidates[0].evidence_fields, ("Annual",))
        self.assertNotIn("Q2-2026", candidates[0].evidence_fields)

    def test_matching_integration_point_end_tag_closes_the_scope(self):
        """A correct </foreignObject> returns to bare SVG content.

        The first template is inside the integration point and is suppressed; the
        second is an ordinary SVG element of that name, so the anchor a breakout
        tag pulls out of it stays a candidate.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<svg>'
            '<foreignObject>'
            '<template><a href="/hidden.pdf">Q2-2026</a></template>'
            '</foreignObject>'
            '<template><div><a href="/release.pdf">Annual report</a></div></template>'
            '</svg>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual report",))

    def test_nested_integration_points_keep_template_suppression(self):
        """annotation-xml holding an SVG foreignObject is still HTML content."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<math><annotation-xml><svg><foreignObject>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</foreignObject></svg></annotation-xml></math>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_mismatched_integration_point_nesting_fails_closed(self):
        """A malformed close over an inner integration point must not open evidence.

        </foreignObject> arrives while <mi> is the open integration point. The
        scanner refuses to pop on a mismatch instead of guessing which scope the
        end tag belongs to, so the region keeps HTML template semantics and the
        anchor stays suppressed.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg><foreignObject><mi></foreignObject>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_foreign_template_end_tag_does_not_release_html_template_scope(self):
        """An SVG <template>'s end tag closes that element, not the HTML ancestor.

        The inner template is an ordinary SVG element of that name, so its
        </template> must close it rather than decrement the enclosing HTML
        template's suppression scope. The <div> breaks out of foreign content,
        but the anchor is still inside the outer HTML template and must stay
        suppressed.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">'
            '<template><svg>'
            '<template></template>'
            '<div><a href="/release.pdf">Q2-2026</a></div>'
            '</svg></template>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ())

    def test_foreign_template_end_tag_holds_scope_for_other_breakout_tags(self):
        """The rule is the template's namespace, not the particular breakout tag."""

        for tag in ("span", "b", "p", "table"):
            with self.subTest(tag=tag):
                candidates = extract_results_page_candidates(
                    self._source(),
                    '<a href="/q">'
                    '<template><svg>'
                    '<template></template>'
                    f'<{tag}><a href="/release.pdf">Q2-2026</a></{tag}>'
                    '</svg></template>'
                    '</a>',
                )
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
                self.assertEqual(candidates[0].evidence_fields, ())

    def test_plain_html_template_still_closes_normally(self):
        """The ordinary case keeps working: </template> ends the suppression."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<template><a href="/hidden.pdf">Q2-2026</a></template>'
            '<a href="/release.pdf">Annual report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual report",))

    def test_bare_foreign_template_pair_leaves_html_scope_untouched(self):
        """A whole <svg><template></template> pair must not open or close anything."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<svg><template></template></svg>'
            '<a href="/release.pdf">Annual report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual report",))

    def test_nested_html_templates_release_one_scope_at_a_time(self):
        """Each </template> closes exactly one opener, so the outer one survives."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<template>'
            '<template><a href="/inner.pdf">Q2-2026</a></template>'
            '<a href="/outer.pdf">Q2-2026</a>'
            '</template>'
            '<a href="/release.pdf">Annual report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual report",))

    def test_stray_template_end_tag_does_not_release_a_later_html_template(self):
        """A </template> with nothing open must not leave the state owing a close."""

        candidates = extract_results_page_candidates(
            self._source(),
            '</template></template>'
            '<a href="/q">'
            '<template><svg>'
            '<template></template>'
            '<div><a href="/release.pdf">Q2-2026</a></div>'
            '</svg></template>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ())

    def test_unclosed_foreign_template_does_not_swallow_the_html_close(self):
        """Closing the SVG root also closes the templates it still held open."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<template><svg><template></svg></template>'
            '<a href="/release.pdf">Annual report</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Annual report",))

    def test_foreign_root_end_tag_pops_through_its_matching_opener(self):
        """</svg> closes every foreign root it still holds open, not just one level.

        HTML5 walks the open elements to the matching svg opener and pops
        through it, so the <template> after it is back in HTML content and its
        anchor is template-local.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg><math></svg>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_foreign_root_end_tag_pops_through_the_other_nesting_order(self):
        """The rule is the matching opener, not a preference for svg over math."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<math><svg></math>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_foreign_roots_closed_in_order_leave_html_content(self):
        """The well-formed LIFO close still ends up back in HTML content."""

        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg><math></math></svg>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_unmatched_foreign_root_end_tag_leaves_the_scope_alone(self):
        """</math> with no math open is ignored rather than guessed at.

        The SVG root stays open, so the breakout tag is what returns the
        following template to HTML content.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg></math><div>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</div></svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_self_closing_child_of_an_integration_point_is_parsed_as_html(self):
        """Inside foreignObject the slash is ignored, so the inner element opens.

        The first </foreignObject> then closes that inner HTML element and the
        outer SVG integration point stays active, which keeps the following
        <template> an HTML template.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/q">Report'
            '<svg><foreignObject>'
            '<foreignObject/></foreignObject>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</foreignObject></svg>'
            '</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
        self.assertEqual(candidates[0].evidence_fields, ("Report",))

    def test_self_closing_meaning_follows_the_parse_mode_not_the_tag_name(self):
        """Every integration-point name behaves this way inside an integration point."""

        for tag in ("desc", "mi", "mtext", "title", "annotation-xml"):
            with self.subTest(tag=tag):
                candidates = extract_results_page_candidates(
                    self._source(),
                    '<a href="/q">Report'
                    '<svg><foreignObject>'
                    f'<{tag}/></{tag}>'
                    '<template><a href="/release.pdf">Q2-2026</a></template>'
                    '</foreignObject></svg>'
                    '</a>',
                )
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].source_url, "https://investor.example.com/q")
                self.assertEqual(candidates[0].evidence_fields, ("Report",))

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


class ResultsPageReleaseScopeClassificationTests(unittest.TestCase):
    """Classification of anchor occurrences, at the scanner rather than the tree.

    html5lib sometimes leaves a misclassified anchor nested where the visible
    walk drops it anyway, so a leak is not always observable in the candidates.
    These pin the classification itself, which is what suppression relies on.
    """

    def _template_local(self, html: str, href: str = "/release.pdf") -> list[bool]:
        scanner = _RawAnchorHrefSafetyScanner()
        scanner.feed(html)
        scanner.close()
        return scanner.anchor_occurrences.get(href, [])

    def test_self_closing_child_of_integration_point_keeps_the_scope_open(self):
        self.assertEqual(
            self._template_local(
                '<svg><foreignObject><foreignObject/></foreignObject>'
                '<template><a href="/release.pdf">Q2-2026</a></template>'
                '</foreignObject></svg>'
            ),
            [True],
        )

    def test_bare_self_closing_foreign_element_still_closes_its_scope(self):
        self.assertEqual(
            self._template_local(
                '<svg><foreignObject/>'
                '<template><a href="/release.pdf">Q2-2026</a></template>'
                '</svg>'
            ),
            [False],
        )

    def test_foreign_root_end_tag_pops_through_the_matching_opener(self):
        self.assertEqual(
            self._template_local(
                '<svg><math></svg>'
                '<template><a href="/release.pdf">Q2-2026</a></template>'
            ),
            [True],
        )

    def test_unmatched_foreign_root_end_tag_changes_nothing(self):
        self.assertEqual(
            self._template_local(
                '<svg></math>'
                '<template><a href="/release.pdf">Q2-2026</a></template>'
            ),
            [False],
        )

    def test_mismatched_integration_point_close_keeps_the_scope_open(self):
        self.assertEqual(
            self._template_local(
                '<svg><foreignObject><mi></foreignObject>'
                '<template><a href="/release.pdf">Q2-2026</a></template>'
                '</svg>'
            ),
            [True],
        )

    def test_bare_foreign_template_stays_foreign(self):
        self.assertEqual(
            self._template_local(
                '<svg><template><div><a href="/release.pdf">Q2-2026</a></div></template></svg>'
            ),
            [False],
        )

    def test_foreign_elements_hold_markup_while_html_text_elements_do_not(self):
        # <svg><script> is a foreign element whose children are markup, so the
        # anchor is recorded; inside foreignObject the same tag is HTML raw text.
        self.assertEqual(
            self._template_local('<svg><script><a href="/release.pdf">Q2-2026</a></script></svg>'),
            [False],
        )
        self.assertEqual(
            self._template_local(
                '<svg><foreignObject><script><a href="/release.pdf">Q2-2026</a></script>'
                '</foreignObject></svg>'
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
