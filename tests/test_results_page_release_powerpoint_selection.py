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

    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results",
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

    def test_composite_powerpoint_link_title_is_excluded(self):
        pdf = self._candidate("https://investor.example.com/results.pdf", "PDF")
        ppt = self._candidate(
            "https://investor.example.com/presentation.pptx",
            "Download PPT PPT",
        )

        selection = select_results_page_release_candidate(self._event(), (pdf, ppt))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)

    def test_duplicate_url_preserves_all_link_local_titles(self):
        html = """
        <a href="/presentation">Download</a>
        <a href="/presentation" aria-label="Download PPT">PPT</a>
        """

        candidates = extract_results_page_candidates(self._source(), html)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, "https://investor.example.com/presentation")
        self.assertEqual(candidates[0].source_title, "Download Download PPT PPT")

    def test_duplicate_generic_then_powerpoint_title_does_not_remain_ambiguous(self):
        html = """
        <a href="/presentation">Download</a>
        <table><tr>
          <td>26 Aug 2026</td>
          <td><a href="/results.pdf">PDF</a></td>
          <td><a href="/presentation" aria-label="Download PPT">PPT</a></td>
        </tr></table>
        """

        candidates = extract_results_page_candidates(self._source(), html)
        selection = select_results_page_release_candidate(self._event(), candidates)

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate.source_url, "https://investor.example.com/results.pdf")

    def test_fragmented_row_ppt_field_does_not_exclude_pdf(self):
        html = """
        <table><tr>
          <td>26 Aug 2026</td>
          <td><a href="/results.pdf">PDF</a></td>
          <td><img alt="separator"></td>
          <td><a href="/presentation.pptx">PPT</a></td>
        </tr></table>
        """

        candidates = extract_results_page_candidates(self._source(), html)
        pdf = next(candidate for candidate in candidates if candidate.source_url.endswith("results.pdf"))
        self.assertIn("PPT", pdf.evidence_fields)

        selection = select_results_page_release_candidate(self._event(), candidates)

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, pdf)

    def test_repeated_powerpoint_labels_on_distinct_presentations_are_excluded(self):
        pdf = self._candidate("https://investor.example.com/results.pdf", "PDF")
        first_ppt = self._candidate("https://investor.example.com/presentation-a", "PPT")
        second_ppt = self._candidate("https://investor.example.com/presentation-b", "PPT")

        selection = select_results_page_release_candidate(
            self._event(),
            (pdf, first_ppt, second_ppt),
        )

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
