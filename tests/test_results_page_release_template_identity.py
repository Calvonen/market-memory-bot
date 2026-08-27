from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import (
    _TEMPLATE_MARKER_PREFIX,
    _template_local_occurrence_flags,
    extract_results_page_candidates,
)


class _Base(unittest.TestCase):
    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results/index.html",
            version=1,
        )

    def _flags(self, html: str, href: str = "/release.pdf") -> list[bool]:
        return _template_local_occurrence_flags(html).get(href, [])

    def _urls(self, html: str) -> list[str]:
        return [c.source_url for c in extract_results_page_candidates(self._source(), html)]


class TemplateTokenIdentityTests(_Base):
    """Template tokens are matched by marker, never by tree preorder position."""

    def test_foster_parented_template_is_not_matched_by_preorder(self):
        """Foster parenting can move a later template ahead of an earlier one.

        The counts still agree, so the count guard cannot catch it; only an
        identity that travels with the element can.
        """
        html = (
            "<table><tr><td><svg><template></template></svg></td></tr>"
            '<template><a href="/release.pdf">Q2-2026</a></template></table>'
        )
        self.assertEqual(self._flags(html), [True])
        self.assertNotIn("https://investor.example.com/release.pdf", self._urls(html))

    def test_count_matching_reorder_still_maps_each_token_to_its_element(self):
        """The shape where tree preorder really is ['html', 'svg'] but source is svg, html."""

        html = (
            '<a href="/q">Report<table><tr><td><svg><template></template></svg></td></tr>'
            '<template><a href="/release.pdf">Q2-2026</a></template></table></a>'
        )
        self.assertEqual(self._flags(html), [True])
        self.assertEqual(self._urls(html), ["https://investor.example.com/q"])

    def test_same_href_visible_and_in_a_foster_parented_template(self):
        """The rendered spelling survives; the foster-parented one is suppressed."""

        html = (
            '<a href="/release.pdf">Annual report</a>'
            '<a href="/q">R<table><tr><td><svg><template></template></svg></td></tr>'
            '<template><a href="/release.pdf">Q2-2026</a></template></table></a>'
        )
        self.assertEqual(self._flags(html), [False, True])
        candidates = extract_results_page_candidates(self._source(), html)
        release = next(
            c for c in candidates if c.source_url == "https://investor.example.com/release.pdf"
        )
        self.assertEqual(release.evidence_fields, ("Annual report",))
        self.assertNotIn("Q2-2026", release.evidence_fields)

    def test_foreign_html_foreign_template_triple(self):
        """A template between two foreign ones keeps its own identity."""

        html = (
            '<a href="/q">R<table><tr><td><svg><template></template></svg></td></tr>'
            '<template><a href="/release.pdf">Q2-2026</a></template>'
            "<tr><td><math><template></template></math></td></tr></table></a>"
        )
        self.assertEqual(self._flags(html), [True])
        self.assertNotIn("https://investor.example.com/release.pdf", self._urls(html))

    def test_a_dropped_template_fails_closed(self):
        """html5lib dropping a template breaks the 1:1 match, so every token counts as HTML."""

        html = (
            "<select><template></template></select>"
            '<svg><template><div><a href="/release.pdf">Q2-2026</a></div></template></svg>'
        )
        self.assertEqual(self._flags(html), [True])
        self.assertNotIn("https://investor.example.com/release.pdf", self._urls(html))

    def test_a_marker_shaped_source_attribute_cannot_steer_the_match(self):
        """A page spelling the reserved prefix is refused instrumentation, not trusted."""

        for attribute in (
            f'{_TEMPLATE_MARKER_PREFIX}deadbeef="7"',
            f'{_TEMPLATE_MARKER_PREFIX.upper()}DEADBEEF="0"',
            f'{_TEMPLATE_MARKER_PREFIX}x="not-a-number"',
        ):
            with self.subTest(attribute=attribute):
                html = (
                    f"<svg><template {attribute}>"
                    '<div><a href="/release.pdf">Q2-2026</a></div>'
                    "</template></svg>"
                )
                # Without the spoof this template is foreign and the anchor renders;
                # the spoof can only push it the fail-closed way.
                self.assertEqual(self._flags(html), [True])
                self.assertNotIn("https://investor.example.com/release.pdf", self._urls(html))

    def test_the_marker_never_reaches_a_candidate(self):
        html = (
            "<table><tr><td><svg><template></template></svg></td></tr>"
            '<template><a href="/release.pdf">Q2-2026</a></template></table>'
            '<a href="/visible.pdf">Annual <b>report</b></a>'
        )
        for candidate in extract_results_page_candidates(self._source(), html):
            blob = " ".join(
                [candidate.source_url, candidate.source_title or "", *candidate.evidence_fields]
            ).lower()
            self.assertNotIn(_TEMPLATE_MARKER_PREFIX, blob)
            self.assertNotIn("data-mmb", blob)

    def test_instrumentation_does_not_change_raw_href_safety(self):
        """Href safety is analysed on the original source and stays unchanged."""

        for html in (
            '<template></template><a href="/rel&#9;ease.pdf">Q2-2026</a>',
            '<template></template><a href="/a.pdf" href="/b.pdf">Q2-2026</a>',
            '<a href="/rel&#10;ease.pdf">Q2-2026</a><template></template>',
        ):
            with self.subTest(html=html):
                self.assertEqual(self._urls(html), [])


class StaleForeignRootTests(_Base):
    """A foreign root the scanner still holds cannot release an HTML template."""

    def test_breakout_then_stray_foreign_close_keeps_the_template_suppressed(self):
        """<div> pops the SVG root in html5lib, so the later </svg> is stray.

        The scanner deliberately does not model breakouts, so its root is stale.
        Retiring templates at that root must therefore never remove an HTML one.
        """
        html = (
            '<a href="/q">Report<svg><div></div><template></svg>'
            '<a href="/release.pdf">Q2-2026</a></template></a>'
        )
        self.assertEqual(self._flags(html), [True])
        self.assertEqual(self._urls(html), ["https://investor.example.com/q"])

    def test_stale_close_does_not_suppress_later_rendered_content(self):
        """Fail-closed here must stay local: ordinary links after it still surface."""

        html = (
            '<a href="/q">Report<svg><div></div><template></svg>'
            '<a href="/release.pdf">Q2-2026</a></template></a>'
            '<a href="/visible.pdf">Annual report</a>'
        )
        self.assertEqual(self._flags(html), [True])
        self.assertEqual(self._flags(html, "/visible.pdf"), [False])
        self.assertIn("https://investor.example.com/visible.pdf", self._urls(html))

    def test_a_bare_valid_foreign_close_still_works(self):
        html = '<svg><template></template></svg><a href="/release.pdf">Annual report</a>'
        self.assertEqual(self._flags(html), [False])
        self.assertEqual(self._urls(html), ["https://investor.example.com/release.pdf"])

    def test_unclosed_foreign_template_is_retired_by_its_root(self):
        """A foreign token is still retired at its root, so the HTML close lands."""

        html = (
            "<template><svg><template></svg></template>"
            '<a href="/release.pdf">Annual report</a>'
        )
        self.assertEqual(self._flags(html), [False])
        self.assertEqual(self._urls(html), ["https://investor.example.com/release.pdf"])

    def test_text_only_elements_stay_aligned_across_the_namespace_boundary(self):
        """The foreign-root stack still exists for this, and only this."""

        for tag in ("title", "textarea", "script", "style"):
            with self.subTest(tag=tag):
                html = (
                    f'<a href="/q">Report<svg><{tag}>'
                    '<template><a href="/release.pdf">Q2-2026</a></template>'
                    f"</{tag}></svg></a>"
                )
                self.assertNotIn("https://investor.example.com/release.pdf", self._urls(html))

    def test_ordinary_rendered_links_are_never_over_suppressed(self):
        html = (
            '<div><a href="/a.pdf">Annual</a></div>'
            '<table><tr><td><a href="/b.pdf">Interim</a></td></tr></table>'
        )
        self.assertEqual(
            self._urls(html),
            ["https://investor.example.com/a.pdf", "https://investor.example.com/b.pdf"],
        )


if __name__ == "__main__":
    unittest.main()
