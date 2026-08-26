from __future__ import annotations

import unittest

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import (
    _RawAnchorHrefSafetyScanner,
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
        scanner = _RawAnchorHrefSafetyScanner()
        scanner.feed(html)
        scanner.close()

        self.assertEqual(scanner.anchor_occurrences["/release.pdf"], [True])
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
        scanner = _RawAnchorHrefSafetyScanner()
        scanner.feed(html)
        scanner.close()

        self.assertEqual(scanner.anchor_occurrences["/release.pdf"], [False])
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
                scanner = _RawAnchorHrefSafetyScanner()
                scanner.feed(html)
                scanner.close()
                self.assertEqual(scanner.anchor_occurrences["/release.pdf"], [True])


if __name__ == "__main__":
    unittest.main()
