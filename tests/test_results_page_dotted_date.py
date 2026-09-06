from __future__ import annotations

import unittest
from datetime import date

from trading_system.results_page_release_candidates import ResultsPageReleaseCandidate
from trading_system.results_page_release_selection import (
    ResultsPageSelectionContext,
    ResultsPageSelectionStatus,
    select_results_page_release_candidate,
)


class ResultsPageDottedDateTests(unittest.TestCase):
    def test_selects_day_first_dotted_date_from_row_evidence(self):
        target = ResultsPageSelectionContext(
            calendar_event_id="calendar:syrah-test",
            scheduled_date=date(2026, 9, 7),
        )
        candidate = ResultsPageReleaseCandidate(
            event_id="calendar:syrah-test",
            source_url="https://investor.example.com/half-year-results.pdf",
            source_title="PDF",
            evidence_fields=("07.09.2026 Half Year Results PDF",),
        )

        selection = select_results_page_release_candidate(target, (candidate,))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, candidate)

    def test_dotted_date_does_not_match_adjacent_wrong_row(self):
        target = ResultsPageSelectionContext(
            calendar_event_id="calendar:syrah-test",
            scheduled_date=date(2026, 9, 7),
        )
        wrong = ResultsPageReleaseCandidate(
            event_id="calendar:syrah-test",
            source_url="https://investor.example.com/older-results.pdf",
            source_title="PDF",
            evidence_fields=("06.09.2026 Older Results PDF",),
        )

        selection = select_results_page_release_candidate(target, (wrong,))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.NO_MATCH)

    def test_dotted_date_rejects_embedding_in_longer_numeric_value(self):
        target = ResultsPageSelectionContext(
            calendar_event_id="calendar:syrah-test",
            scheduled_date=date(2026, 9, 7),
        )
        candidate = ResultsPageReleaseCandidate(
            event_id="calendar:syrah-test",
            source_url="https://investor.example.com/release.pdf",
            source_title="PDF",
            evidence_fields=("107.09.20260 Results PDF",),
        )

        selection = select_results_page_release_candidate(target, (candidate,))

        self.assertEqual(selection.status, ResultsPageSelectionStatus.NO_MATCH)


if __name__ == "__main__":
    unittest.main()
