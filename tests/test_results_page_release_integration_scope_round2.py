from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import (
    _template_local_occurrence_flags,
    extract_results_page_candidates,
)


class ResultsPageReleaseIntegrationScopeRound2Tests(unittest.TestCase):
    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results/index.html",
            version=1,
        )

    def test_special_html_child_keeps_outer_foreignobject_integration_scope_open(self):
        html = (
            '<svg><foreignObject><div></foreignObject>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            '</foreignObject></svg>'
        )
        self.assertEqual(_template_local_occurrence_flags(html)["/release.pdf"], [True])
        candidates = extract_results_page_candidates(self._source(), html)
        self.assertNotIn(
            "https://investor.example.com/release.pdf",
            [candidate.source_url for candidate in candidates],
        )

    def test_annotation_xml_application_xml_is_not_html_integration_point(self):
        html = (
            '<math><annotation-xml encoding="application/xml">'
            '<template><div><a href="/release.pdf">Q2-2026</a></div></template>'
            '</annotation-xml></math>'
        )
        self.assertEqual(_template_local_occurrence_flags(html)["/release.pdf"], [False])
        candidates = extract_results_page_candidates(self._source(), html)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_annotation_xml_html_encodings_remain_integration_points(self):
        for encoding in ("text/html", "TEXT/HTML", "application/xhtml+xml"):
            with self.subTest(encoding=encoding):
                html = (
                    f'<math><annotation-xml encoding="{encoding}">'
                    '<template><a href="/release.pdf">Q2-2026</a></template>'
                    '</annotation-xml></math>'
                )
                self.assertEqual(_template_local_occurrence_flags(html)["/release.pdf"], [True])


    def test_every_non_void_html_special_child_keeps_the_integration_scope_open(self):
        """The barrier question is html5lib's, so no allowlist can be incomplete.

        `dir` was missing from the scanner's hand-maintained special-element set.
        The set is gone: the template's namespace is read off the tree, which
        already applies the whole special category.
        """
        for tag in (
            "dir", "div", "p", "section", "article", "aside", "blockquote", "center",
            "details", "dialog", "dl", "fieldset", "figure", "footer", "form", "h1",
            "header", "hgroup", "li", "listing", "main", "marquee", "menu", "nav", "ol",
            "plaintext", "pre", "summary", "table", "ul", "xmp",
        ):
            with self.subTest(tag=tag):
                html = (
                    f'<svg><foreignObject><{tag}></foreignObject>'
                    '<template><a href="/release.pdf">Q2-2026</a></template>'
                    '</foreignObject></svg>'
                )
                self.assertEqual(_template_local_occurrence_flags(html)["/release.pdf"], [True])
                self.assertNotIn(
                    "https://investor.example.com/release.pdf",
                    [c.source_url for c in extract_results_page_candidates(self._source(), html)],
                )

    def test_annotation_xml_encoding_must_match_exactly(self):
        """Whitespace is part of the attribute value, so a padded encoding is not one."""

        for encoding, is_integration_point in (
            ("text/html", True),
            ("TEXT/HTML", True),
            ("Text/Html", True),
            ("application/xhtml+xml", True),
            ("APPLICATION/XHTML+XML", True),
            (" text/html ", False),
            ("text/html ", False),
            (" application/xhtml+xml", False),
            ("application/xml", False),
            ("text/plain", False),
        ):
            with self.subTest(encoding=encoding):
                html = (
                    f'<math><annotation-xml encoding="{encoding}">'
                    '<template><div><a href="/release.pdf">Q2-2026</a></div></template>'
                    '</annotation-xml></math>'
                )
                self.assertEqual(
                    _template_local_occurrence_flags(html)["/release.pdf"],
                    [is_integration_point],
                )

    def test_mathml_text_integration_points_honour_their_child_exceptions(self):
        """mglyph and malignmark stay in foreign content; other children do not."""

        for parent in ("mi", "mn", "mo", "ms", "mtext"):
            for child, stays_foreign in (("mglyph", True), ("malignmark", True), ("span", False)):
                with self.subTest(parent=parent, child=child):
                    html = (
                        f'<math><{parent}><{child}>'
                        '<template><div><a href="/release.pdf">Q2-2026</a></div></template>'
                        f'</{child}></{parent}></math>'
                    )
                    self.assertEqual(
                        _template_local_occurrence_flags(html)["/release.pdf"],
                        [not stays_foreign],
                    )

    def test_mglyph_template_keeps_its_rendered_anchor(self):
        """A foreign template must not over-suppress a genuinely rendered link."""

        html = (
            '<math><mtext><mglyph>'
            '<template><div><a href="/release.pdf">Q2-2026</a></div></template>'
            '</mglyph></mtext></math>'
        )
        candidates = extract_results_page_candidates(self._source(), html)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_implied_close_retires_the_barrier_it_opened(self):
        """<div> implicitly closes an open <p>, so no stale barrier survives.

        The scanner used to keep the <p> as a barrier for the rest of the
        integration point and over-suppress the genuinely rendered anchor after
        it. There is no barrier state left to go stale.
        """
        html = (
            '<svg><foreignObject><p><div></div></foreignObject>'
            '<template><div><a href="/release.pdf">Q2-2026</a></div></template>'
            '</svg>'
        )
        self.assertEqual(_template_local_occurrence_flags(html)["/release.pdf"], [False])
        candidates = extract_results_page_candidates(self._source(), html)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/release.pdf")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))

    def test_implied_close_variants_do_not_leave_stale_scope(self):
        """Any implied close behaves the same, because none of it is tracked."""

        for opener, closer in (("p", "div"), ("li", "li"), ("dt", "dd"), ("p", "table")):
            with self.subTest(opener=opener, closer=closer):
                html = (
                    f'<svg><foreignObject><{opener}><{closer}></{closer}></foreignObject>'
                    '<template><div><a href="/release.pdf">Q2-2026</a></div></template>'
                    '</svg>'
                )
                self.assertEqual(
                    _template_local_occurrence_flags(html)["/release.pdf"], [False]
                )


if __name__ == "__main__":
    unittest.main()
