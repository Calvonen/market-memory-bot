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
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/r">Q2<svg><foreignObject></br>-2026</foreignObject></svg></a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2 -2026",))

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

    def test_foreign_template_endtag_cannot_close_html_template_ancestor(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<template><svg></template><a href="/r">Q2-2026</a></svg></template>',
        )
        self.assertEqual(candidates, ())


if __name__ == "__main__":
    unittest.main()
