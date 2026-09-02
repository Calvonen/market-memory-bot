from __future__ import annotations

import unittest
from datetime import date

from trading_system.calendar_repository import CalendarEvent, CalendarEventStatus
from trading_system.results_page_release_candidates import ResultsPageReleaseCandidate
from trading_system.results_page_release_selection import (
    ResultsPageSelectionStatus,
    select_results_page_release_candidate,
)


class ResultsPageReleasePowerPointSelectionTests(unittest.TestCase):
    def _event(self) -> CalendarEvent:
        return CalendarEvent(
            calendar_event_id="calendar:test-event",
            company_name="Example Plc",
            instrument="EXAMPLE",
            market="London",
            event_type="half_year_results",
            scheduled_date=date(2026, 8, 26),
            source="calendar",
            occurrence_key="2026-08-26",
            status=CalendarEventStatus.TRACKED,
        )

    def _candidate(self, url: str, title: str) -> ResultsPageReleaseCandidate:
        return ResultsPageReleaseCandidate(
            event_id="calendar:test-event",
            source_url=url,
            source_title=title,
            evidence_fields=(title, "26 Aug 2026 Half Year Results PDF PPT"),
        )

    def test_pdf_wins_when_same_results_row_also_has_explicit_ppt(self):
        pdf = self._candidate("https://investor.example.com/results.pdf", "PDF")
        ppt = self._candidate("https://investor.example.com/presentation.pptx", "PPT")

        selection = select_results_page_release_candidate(self._event(), (pdf, ppt))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)

    def test_pdf_wins_when_powerpoint_title_is_composite_accessibility_text(self):
        pdf = self._candidate("https://investor.example.com/results.pdf", "PDF")
        ppt = self._candidate(
            "https://investor.example.com/presentation.pptx",
            "Download PPT PPT",
        )

        selection = select_results_page_release_candidate(self._event(), (pdf, ppt))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)

    def test_duplicate_link_powerpoint_evidence_is_not_lost_with_generic_first_title(self):
        pdf = self._candidate("https://investor.example.com/results.pdf", "PDF")
        ppt = ResultsPageReleaseCandidate(
            event_id="calendar:test-event",
            source_url="https://investor.example.com/presentation",
            source_title="Download",
            evidence_fields=(
                "Download",
                "PPT",
                "26 Aug 2026 Half Year Results PDF PPT",
            ),
        )

        selection = select_results_page_release_candidate(self._event(), (pdf, ppt))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)

    def test_composite_duplicate_link_powerpoint_evidence_is_recognized(self):
        shared_row = "26 Aug 2026 Half Year Results PDF PPT"
        pdf = ResultsPageReleaseCandidate(
            event_id="calendar:test-event",
            source_url="https://investor.example.com/results.pdf",
            source_title="Download",
            evidence_fields=("Download", shared_row),
        )
        ppt = ResultsPageReleaseCandidate(
            event_id="calendar:test-event",
            source_url="https://investor.example.com/presentation",
            source_title="Download",
            evidence_fields=("Download", "Download PPT", shared_row),
        )

        selection = select_results_page_release_candidate(self._event(), (pdf, ppt))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)

    def test_row_evidence_with_pdf_and_ppt_does_not_exclude_pdf(self):
        pdf = ResultsPageReleaseCandidate(
            event_id="calendar:test-event",
            source_url="https://investor.example.com/results.pdf",
            source_title="Download",
            evidence_fields=("Download", "26 Aug 2026 Half Year Results PDF PPT"),
        )

        selection = select_results_page_release_candidate(self._event(), (pdf,))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)

    def test_powerpoint_letters_inside_larger_token_do_not_exclude_candidate(self):
        pdf = self._candidate("https://investor.example.com/results.pdf", "PDF")
        other = self._candidate("https://investor.example.com/appendix.pdf", "PPTNotes")

        selection = select_results_page_release_candidate(self._event(), (pdf, other))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.AMBIGUOUS)

    def test_two_non_powerpoint_matches_still_fail_closed(self):
        first = self._candidate("https://investor.example.com/results.pdf", "PDF")
        second = self._candidate("https://investor.example.com/appendix.pdf", "PDF")

        selection = select_results_page_release_candidate(self._event(), (first, second))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.AMBIGUOUS)

    def test_powerpoint_only_match_is_not_selected(self):
        ppt = self._candidate("https://investor.example.com/presentation.pptx", "PowerPoint")

        selection = select_results_page_release_candidate(self._event(), (ppt,))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.NO_MATCH)


if __name__ == "__main__":
    unittest.main()
