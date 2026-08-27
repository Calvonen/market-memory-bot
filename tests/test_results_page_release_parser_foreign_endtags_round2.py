from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


class ResultsPageReleaseParserForeignEndtagRound2Tests(unittest.TestCase):
    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results",
            version=1,
        )

    def test_foreign_br_endtag_breaks_evidence_at_svg_integration_point(self):
        """An SVG integration point is still an evidence boundary for the anchor.

        html5lib matches the spec here: ``foreignObject`` is an HTML integration
        point, so ``</br>`` is reprocessed in place and the ``<br>`` plus its
        trailing text stay inside the SVG subtree. We do not model SVG as a
        rendering engine, so that subtree supplies no evidence to the enclosing
        anchor and the trailing text is dropped rather than merged.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/r">Q2<svg><foreignObject></br>-2026</foreignObject></svg></a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2",))

    def test_foreign_script_and_style_text_remain_suppressed(self):
        for tag in ("script", "style"):
            with self.subTest(tag=tag):
                candidates = extract_results_page_candidates(
                    self._source(),
                    f'<a href="/r"><svg><{tag}>Q2-2026</{tag}></svg></a>',
                )
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
                self.assertEqual(candidates[0].evidence_fields, ())

    def test_foreign_template_endtag_closes_html_template_ancestor(self):
        """``</template>`` inside foreign content does close the HTML template.

        Per HTML5, the foreign "any other end tag" rules walk up to the HTML
        ``template`` ancestor and reprocess the token in HTML content, which pops
        both the ``svg`` element and the template. html5lib agrees, so the anchor
        spelled after ``</template>`` is ordinary rendered content, not template
        content, and stays a release candidate.
        """
        candidates = extract_results_page_candidates(
            self._source(),
            '<template><svg></template><a href="/r">Q2-2026</a></svg></template>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))


if __name__ == "__main__":
    unittest.main()
