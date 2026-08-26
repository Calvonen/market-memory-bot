from __future__ import annotations

import unittest
from datetime import date

from trading_system.calendar_repository import CalendarEvent, CalendarEventStatus
from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import (
    ResultsPageReleaseCandidate,
    extract_results_page_candidates,
)
from trading_system.results_page_release_selection import (
    ResultsPageSelectionStatus,
    select_results_page_release_candidate,
)


class ResultsPageReleaseEvidenceFieldTests(unittest.TestCase):
    def _event(self) -> CalendarEvent:
        return CalendarEvent(
            calendar_event_id="calendar:test-event",
            company_name="Example Oyj",
            instrument="EXAMPLE.HE",
            market="Helsinki",
            event_type="earnings",
            scheduled_date=date(2026, 8, 26),
            source="calendar",
            occurrence_key="2026-08-26",
            status=CalendarEventStatus.TRACKED,
        )

    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results",
            version=1,
        )

    def _select(self, candidate: ResultsPageReleaseCandidate) -> ResultsPageSelectionStatus:
        return select_results_page_release_candidate(
            self._event(),
            (candidate,),
            release_period="Q2 2026",
        ).status

    def test_extractor_preserves_original_anchor_evidence_fields(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf" aria-label="Q2" title="2026">Results</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_title, "Q2 2026 Results")
        self.assertEqual(candidates[0].evidence_fields, ("Q2", "2026", "Results"))
        self.assertEqual(self._select(candidates[0]), ResultsPageSelectionStatus.NO_MATCH)

    def test_combining_marks_are_token_adjacency(self):
        for title in ("A\u0301Q2-2026", "Q2-2026\u0301A"):
            with self.subTest(title=title):
                candidate = ResultsPageReleaseCandidate(
                    event_id="calendar:test-event",
                    source_url="https://investor.example.com/release.pdf",
                    source_title=title,
                )
                self.assertEqual(self._select(candidate), ResultsPageSelectionStatus.NO_MATCH)

    def test_unicode_format_controls_are_token_adjacency(self):
        for title in ("A\u200cQ2-2026", "Q2-2026\u200dA"):
            with self.subTest(title=title):
                candidate = ResultsPageReleaseCandidate(
                    event_id="calendar:test-event",
                    source_url="https://investor.example.com/release.pdf",
                    source_title=title,
                )
                self.assertEqual(self._select(candidate), ResultsPageSelectionStatus.NO_MATCH)

    def test_zero_width_space_is_a_period_token_separator(self):
        candidate = ResultsPageReleaseCandidate(
            event_id="calendar:test-event",
            source_url="https://investor.example.com/release.pdf",
            source_title="Download\u200bQ2-2026",
        )
        self.assertEqual(self._select(candidate), ResultsPageSelectionStatus.SELECTED)

    def test_visible_text_nodes_preserve_adjacency(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf"><span>A</span><span>Q2-2026</span></a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].evidence_fields, ("AQ2-2026",))
        self.assertEqual(self._select(candidates[0]), ResultsPageSelectionStatus.NO_MATCH)

    def test_percent_decoded_url_adjacency_fails_closed(self):
        for url in (
            "https://investor.example.com/Q2-2026%41nnual.pdf",
            "https://investor.example.com/Q2-2026%CC%81A.pdf",
            "https://investor.example.com/Q2-2026%E2%80%8DA.pdf",
        ):
            with self.subTest(url=url):
                candidate = ResultsPageReleaseCandidate(
                    event_id="calendar:test-event",
                    source_url=url,
                )
                self.assertEqual(self._select(candidate), ResultsPageSelectionStatus.NO_MATCH)

    def test_percent_decoded_url_controls_fail_closed(self):
        for url in (
            "https://investor.example.com/Q2-2026%00Annual.pdf",
            "https://investor.example.com/Q2-2026%0AAnnual.pdf",
            "https://investor.example.com/Q2-2026%7FAnnual.pdf",
            "https://investor.example.com/Q2-2026%C2%85Annual.pdf",
        ):
            with self.subTest(url=url):
                candidate = ResultsPageReleaseCandidate(
                    event_id="calendar:test-event",
                    source_url=url,
                )
                self.assertEqual(self._select(candidate), ResultsPageSelectionStatus.NO_MATCH)

    def test_percent_decoded_url_separator_can_supply_standalone_period(self):
        candidate = ResultsPageReleaseCandidate(
            event_id="calendar:test-event",
            source_url="https://investor.example.com/results%20Q2-2026.pdf",
        )
        self.assertEqual(self._select(candidate), ResultsPageSelectionStatus.SELECTED)

    def test_rendered_breaks_preserve_visible_separator(self):
        for html in (
            '<a href="/release.pdf"><span>Download</span><br><span>Q2-2026</span></a>',
            '<a href="/release.pdf"><span>Download</span><hr><span>Q2-2026</span></a>',
            '<a href="/release.pdf"><div>Download</div><div>Q2-2026</div></a>',
            '<a href="/release.pdf"><p>Download</p>Q2-2026</a>',
        ):
            with self.subTest(html=html):
                candidates = extract_results_page_candidates(self._source(), html)
                self.assertEqual(len(candidates), 1)
                self.assertEqual(self._select(candidates[0]), ResultsPageSelectionStatus.SELECTED)

    def test_unmatched_structural_end_tag_does_not_manufacture_separator(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<a href="/release.pdf">Download</div>Q2-2026</a>',
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].evidence_fields, ("DownloadQ2-2026",))
        self.assertEqual(self._select(candidates[0]), ResultsPageSelectionStatus.NO_MATCH)

    def test_non_rendered_subtrees_do_not_supply_evidence(self):
        for tag in ("script", "style", "template"):
            html = f'<a href="/release.pdf">Download<{tag}> Q2-2026 </{tag}>Report</a>'
            with self.subTest(tag=tag):
                candidates = extract_results_page_candidates(self._source(), html)
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].evidence_fields, ("DownloadReport",))
                self.assertEqual(self._select(candidates[0]), ResultsPageSelectionStatus.NO_MATCH)


if __name__ == "__main__":
    unittest.main()
