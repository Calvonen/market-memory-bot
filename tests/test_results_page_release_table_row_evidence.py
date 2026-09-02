from __future__ import annotations

import unittest
from datetime import date

from trading_system.calendar_repository import CalendarEvent, CalendarEventStatus
from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates
from trading_system.results_page_release_selection import (
    ResultsPageSelectionStatus,
    select_results_page_release_candidate,
)


class ResultsPageReleaseTableRowEvidenceTests(unittest.TestCase):
    def _source(self) -> OfficialReleaseSource:
        return OfficialReleaseSource(
            event_id="calendar:test-event",
            source_kind="results_page",
            source_url="https://investor.example.com/results",
            version=1,
        )

    def _event(self) -> CalendarEvent:
        return CalendarEvent(
            calendar_event_id="calendar:test-event",
            company_name="Example plc",
            instrument="EXAMPLE.L",
            market="London",
            event_type="earnings",
            scheduled_date=date(2026, 9, 2),
            source="calendar",
            occurrence_key="2026-09-02",
            status=CalendarEventStatus.TRACKED,
        )

    def test_nearest_table_row_supplies_human_date_evidence(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <table>
              <tr>
                <td>02 Sep 2026</td>
                <td>Half Year Results</td>
                <td><a href="/release.pdf">PDF</a></td>
              </tr>
            </table>
            """,
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.source_title, "PDF")
        self.assertEqual(
            candidate.evidence_fields,
            ("PDF", "02 Sep 2026 Half Year Results PDF"),
        )
        selection = select_results_page_release_candidate(self._event(), candidates)
        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, candidate)

    def test_adjacent_table_row_does_not_leak_evidence(self):
        candidates = extract_results_page_candidates(
            self._source(),
            """
            <table>
              <tr>
                <td>02 Sep 2026</td>
                <td><a href="/other.pdf">Other PDF</a></td>
              </tr>
              <tr>
                <td>01 Sep 2026</td>
                <td>Annual Report</td>
                <td><a href="/release.pdf">PDF</a></td>
              </tr>
            </table>
            """,
        )

        candidate = next(
            candidate
            for candidate in candidates
            if candidate.source_url == "https://investor.example.com/release.pdf"
        )
        self.assertEqual(
            candidate.evidence_fields,
            ("PDF", "01 Sep 2026 Annual Report PDF"),
        )
        self.assertNotIn("02 Sep 2026", " ".join(candidate.evidence_fields))

    def test_non_table_anchor_keeps_link_local_evidence_only(self):
        candidates = extract_results_page_candidates(
            self._source(),
            '<div>02 Sep 2026 <a href="/release.pdf" title="Results">PDF</a></div>',
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_title, "Results PDF")
        self.assertEqual(candidates[0].evidence_fields, ("Results", "PDF"))


if __name__ == "__main__":
    unittest.main()
