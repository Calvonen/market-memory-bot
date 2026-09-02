from __future__ import annotations

import unittest
from datetime import date

from trading_system.results_page_release_candidates import ResultsPageReleaseCandidate
from trading_system.results_page_release_selection import (
    ResultsPageSelectionContext,
    ResultsPageSelectionStatus,
    select_results_page_release_candidate,
)


class ResultsPageReleasePdfPreferenceTests(unittest.TestCase):
    def _event(self) -> ResultsPageSelectionContext:
        return ResultsPageSelectionContext(
            calendar_event_id="calendar:test-event",
            scheduled_date=date(2026, 9, 2),
        )

    def _candidate(self, url: str, title: str, evidence: str = "02 Sep 2026") -> ResultsPageReleaseCandidate:
        return ResultsPageReleaseCandidate(
            event_id="calendar:test-event",
            source_url=url,
            source_title=title,
            evidence_fields=(title, evidence),
        )

    def test_unique_explicit_pdf_breaks_same_date_ambiguity(self):
        pdf = self._candidate("https://investor.example.com/results.pdf", "PDF")
        presentation = self._candidate("https://investor.example.com/presentation.pdf", "PPT")
        webcast = self._candidate("https://investor.example.com/webcast", "Webcast")

        selection = select_results_page_release_candidate(
            self._event(),
            (pdf, presentation, webcast),
        )

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)

    def test_composite_explicit_pdf_title_is_recognized(self):
        pdf = self._candidate("https://investor.example.com/results.pdf", "Download PDF")
        presentation = self._candidate("https://investor.example.com/presentation.pdf", "PPT")

        selection = select_results_page_release_candidate(self._event(), (pdf, presentation))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)

    def test_two_explicit_pdfs_remain_ambiguous(self):
        first = self._candidate("https://investor.example.com/results.pdf", "PDF")
        second = self._candidate("https://investor.example.com/appendix.pdf", "PDF")

        selection = select_results_page_release_candidate(self._event(), (first, second))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.AMBIGUOUS)
        self.assertIsNone(selection.candidate)

    def test_no_explicit_pdf_remains_ambiguous(self):
        presentation = self._candidate("https://investor.example.com/presentation.pdf", "PPT")
        webcast = self._candidate("https://investor.example.com/webcast", "Webcast")

        selection = select_results_page_release_candidate(self._event(), (presentation, webcast))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.AMBIGUOUS)
        self.assertIsNone(selection.candidate)

    def test_unique_explicit_pdf_also_breaks_period_ambiguity(self):
        pdf = self._candidate("https://investor.example.com/results.pdf", "PDF", "H1 2026")
        presentation = self._candidate("https://investor.example.com/presentation.pdf", "PPT", "H1 2026")

        selection = select_results_page_release_candidate(
            ResultsPageSelectionContext(
                calendar_event_id="calendar:test-event",
                scheduled_date=date(2026, 9, 3),
            ),
            (pdf, presentation),
            release_period="H1 2026",
        )

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)


if __name__ == "__main__":
    unittest.main()
