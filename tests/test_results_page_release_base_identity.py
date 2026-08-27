from __future__ import annotations

import unittest
from unittest.mock import patch

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system import results_page_release_candidates as candidates
from trading_system.results_page_release_candidates import (
    _BASE_MARKER_PREFIX,
    _TEMPLATE_MARKER_PREFIX,
    extract_results_page_candidates,
)


ORIGIN = "https://investor.example.com"
ANCHOR = '<a href="r-2026-08-26.pdf">Q2 2026</a>'


class _Base(unittest.TestCase):
    """<base> tokens are matched by marker, never by tree position.

    Tree construction can foster parent a <base> out of a table, or drop it
    entirely. Position is therefore not an identity: a template-local base and a
    rendered one can swap places while the counts still agree, and reading them
    off by position would let an inert base set the document base and repoint
    every release link at a different same-origin document.
    """

    def _source(self, url: str = f"{ORIGIN}/results") -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url=url,
            version=1,
        )

    def _urls(self, body: str, *, page_url: str | None = None) -> tuple[str, ...]:
        return tuple(
            candidate.source_url
            for candidate in extract_results_page_candidates(
                self._source(),
                f"<html><body>{body}{ANCHOR}</body></html>",
                page_url=page_url,
            )
        )

    def _resolved(self, path: str) -> tuple[str, ...]:
        return (f"{ORIGIN}{path}r-2026-08-26.pdf",)


class BaseTokenIdentityTests(_Base):
    def test_codex_foster_parented_table_base_never_lets_the_inert_base_win(self) -> None:
        """The reported case: an inert base first, a foster-parented one second.

        html5lib 1.1 drops the <table>-level <base> in fragment mode, so the
        tokens cannot be proven one-to-one and the page fails closed. What must
        never happen either way is the template-local /inert/ base setting the
        document base.
        """
        urls = self._urls(
            "<table>"
            "<tr><td><template><base href=\"/inert/\"></template></td></tr>"
            '<base href="/real/">'
            "</table>"
        )
        self.assertNotIn(f"{ORIGIN}/inert/r-2026-08-26.pdf", urls)
        self.assertEqual(urls, ())

    def test_identity_survives_a_tree_that_yields_bases_out_of_source_order(self) -> None:
        """The marker, not the position, decides which token each element is.

        A tree builder is free to hand back a later <base> first. Reading the
        elements off in the order the tree yields them would tie the
        template-local marker to the rendered base and let /inert/ win.
        """
        body = (
            '<template><base href="/inert/"></template>'
            '<base href="/real/">'
        )
        self.assertEqual(self._urls(body), self._resolved("/real/"))

        original = candidates._iter_all_tree_bases
        with patch.object(
            candidates,
            "_iter_all_tree_bases",
            lambda root: iter(list(original(root))[::-1]),
        ):
            self.assertEqual(self._urls(body), self._resolved("/real/"))

    def test_first_rendered_base_wins_whatever_order_the_tree_yields(self) -> None:
        body = '<base href="/first/"><base href="/second/">'
        original = candidates._iter_all_tree_bases
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                iterator = (
                    (lambda root: iter(list(original(root))[::-1]))
                    if reverse
                    else original
                )
                with patch.object(candidates, "_iter_all_tree_bases", iterator):
                    self.assertEqual(self._urls(body), self._resolved("/first/"))

    def test_template_local_base_before_a_rendered_base(self) -> None:
        self.assertEqual(
            self._urls('<template><base href="/inert/"></template><base href="/real/">'),
            self._resolved("/real/"),
        )

    def test_rendered_base_before_a_template_local_base(self) -> None:
        self.assertEqual(
            self._urls('<base href="/real/"><template><base href="/inert/"></template>'),
            self._resolved("/real/"),
        )

    def test_foreign_bases_never_set_the_document_base(self) -> None:
        for foreign in ("svg", "math"):
            with self.subTest(foreign=foreign):
                self.assertEqual(
                    self._urls(
                        f'<{foreign}><base href="/inert/"></{foreign}>'
                        '<base href="/real/">'
                    ),
                    self._resolved("/real/"),
                )

    def test_foreign_base_inside_a_foster_parenting_cell(self) -> None:
        self.assertEqual(
            self._urls(
                '<table><tr><td><svg><base href="/inert/"></svg></td></tr></table>'
                '<base href="/real/">'
            ),
            self._resolved("/real/"),
        )

    def test_table_foster_parenting_variants(self) -> None:
        # A cell keeps its base, so the first rendered base in source order wins.
        for wrapper in (
            '<table><tr><td><base href="/real/"></td></tr></table>',
            '<table><caption><base href="/real/"></caption></table>',
        ):
            with self.subTest(wrapper=wrapper):
                self.assertEqual(self._urls(wrapper), self._resolved("/real/"))

        # These placements are dropped by the tree builder, so the token cannot
        # be tied to an element and the whole page fails closed.
        for wrapper in (
            '<table><base href="/real/"></table>',
            '<table><tbody><base href="/real/"></tbody></table>',
            '<table><tr><base href="/real/"></tr></table>',
            '<table><div><base href="/real/"></div></table>',
        ):
            with self.subTest(wrapper=wrapper):
                self.assertEqual(self._urls(wrapper), ())

    def test_a_dropped_base_fails_the_page_closed_even_beside_a_valid_one(self) -> None:
        self.assertEqual(
            self._urls('<base href="/real/"><table><base href="/dropped/"></table>'),
            (),
        )

    def test_base_without_an_href_keeps_the_identity_mapping_aligned(self) -> None:
        self.assertEqual(
            self._urls('<base target="_blank"><base href="/real/">'),
            self._resolved("/real/"),
        )
        self.assertEqual(
            self._urls(
                '<template><base></template><base target="_blank"><base href="/real/">'
            ),
            self._resolved("/real/"),
        )

    def test_a_missing_or_unreadable_marker_fails_closed(self) -> None:
        original = candidates._marker_token
        for broken in (None, -1, 99):
            with self.subTest(token=broken):
                with patch.object(candidates, "_marker_token", lambda element, marker: broken):
                    self.assertEqual(self._urls('<base href="/real/">'), ())
        # Sanity: the same page resolves once the marker is readable again.
        self.assertIs(candidates._marker_token, original)
        self.assertEqual(self._urls('<base href="/real/">'), self._resolved("/real/"))

    def test_a_duplicated_marker_fails_closed(self) -> None:
        with patch.object(candidates, "_marker_token", lambda element, marker: 0):
            self.assertEqual(
                self._urls('<base href="/real/"><base href="/other/">'),
                (),
            )

    def test_a_source_spelling_a_reserved_marker_prefix_fails_closed(self) -> None:
        for attribute in (
            f'{_BASE_MARKER_PREFIX}deadbeef="0"',
            f'{_BASE_MARKER_PREFIX.upper()}DEADBEEF="0"',
            f'{_TEMPLATE_MARKER_PREFIX}deadbeef="0"',
        ):
            with self.subTest(attribute=attribute):
                self.assertEqual(
                    self._urls(f'<div {attribute}></div><base href="/real/">'),
                    (),
                )

    def test_the_base_marker_never_reaches_a_candidate(self) -> None:
        html = (
            "<html><body>"
            '<base href="/downloads/">'
            '<template><base href="/inert/"></template>'
            '<a href="r-2026-08-26.pdf">Annual <b>report</b></a>'
            "</body></html>"
        )
        found = extract_results_page_candidates(self._source(), html)
        self.assertEqual(len(found), 1)
        for candidate in found:
            blob = " ".join(
                [candidate.source_url, candidate.source_title or "", *candidate.evidence_fields]
            ).lower()
            self.assertNotIn(_BASE_MARKER_PREFIX, blob)
            self.assertNotIn("data-mmb", blob)

    def test_base_instrumentation_does_not_change_raw_href_safety(self) -> None:
        for body in (
            '<base href="/downloads/"><a href="/rel&#9;ease.pdf">Q2-2026</a>',
            '<base href="/downloads/"><a href="/a.pdf" href="/b.pdf">Q2-2026</a>',
        ):
            with self.subTest(body=body):
                html = f"<html><body>{body}</body></html>"
                self.assertEqual(extract_results_page_candidates(self._source(), html), ())

    def test_base_identity_holds_against_the_redirected_page_url(self) -> None:
        self.assertEqual(
            self._urls(
                '<template><base href="/inert/"></template><base href="downloads/">',
                page_url=f"{ORIGIN}/investors/results/",
            ),
            self._resolved("/investors/results/downloads/"),
        )


if __name__ == "__main__":
    unittest.main()
