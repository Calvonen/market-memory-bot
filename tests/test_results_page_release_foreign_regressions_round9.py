from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


class ResultsPageReleaseForeignRegressionRound9Tests(unittest.TestCase):
    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results",
            version=1,
        )

    def test_foreign_br_endtag_breakout_preserves_rendered_break(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/r">Q2<svg></br>-2026</svg></a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2 -2026",))

    def test_foreign_template_is_not_html_suppression_after_breakout(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<svg><template><div><a href="/r">Q2-2026</a></div></template></svg>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/r")
        self.assertEqual(candidates[0].evidence_fields, ("Q2-2026",))


if __name__ == "__main__":
    unittest.main()
