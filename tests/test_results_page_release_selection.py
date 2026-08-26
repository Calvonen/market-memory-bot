from __future__ import annotations

import unittest
from datetime import date

from trading_system.calendar_repository import CalendarEvent, CalendarEventStatus
from trading_system.results_page_release_candidates import ResultsPageReleaseCandidate
from trading_system.results_page_release_selection import (
    ResultsPageSelectionStatus,
    select_results_page_release_candidate,
)


class ResultsPageReleaseSelectionTests(unittest.TestCase):
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

    def _candidate(self, url: str, title: str | None = None, *, event_id: str = "calendar:test-event") -> ResultsPageReleaseCandidate:
        return ResultsPageReleaseCandidate(event_id=event_id, source_url=url, source_title=title)

    def test_selects_unique_candidate_with_iso_date_in_url(self):
        selection = select_results_page_release_candidate(
            self._event(),
            (
                self._candidate("https://investor.example.com/reports/2026-08-26-results.pdf", "Results"),
                self._candidate("https://investor.example.com/reports/2025-08-26-results.pdf", "Old results"),
            ),
        )
        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)

    def test_accepts_compact_slash_and_underscore_date_spellings(self):
        for url in (
            "https://investor.example.com/20260826/results.pdf",
            "https://investor.example.com/2026/08/26/results.pdf",
            "https://investor.example.com/2026_08_26/results.pdf",
        ):
            with self.subTest(url=url):
                selection = select_results_page_release_candidate(self._event(), (self._candidate(url),))
                self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)

    def test_title_can_supply_the_exact_scheduled_date(self):
        selection = select_results_page_release_candidate(
            self._event(),
            (self._candidate("https://investor.example.com/release.pdf", "Results 2026-08-26"),),
        )
        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)

    def test_rejects_date_tokens_embedded_in_longer_numeric_values(self):
        for url in (
            "https://investor.example.com/id/20260826001.pdf",
            "https://investor.example.com/id/120260826.pdf",
            "https://investor.example.com/id/2026-08-260.pdf",
            "https://investor.example.com/id/12026_08_26.pdf",
        ):
            with self.subTest(url=url):
                selection = select_results_page_release_candidate(self._event(), (self._candidate(url),))
                self.assertEqual(selection.status, ResultsPageSelectionStatus.NO_MATCH)

    def test_returns_no_match_without_exact_scheduled_date_or_explicit_period(self):
        selection = select_results_page_release_candidate(
            self._event(),
            (self._candidate("https://investor.example.com/q2-2026-results.pdf", "Q2 2026 results"),),
        )
        self.assertEqual(selection.status, ResultsPageSelectionStatus.NO_MATCH)

    def test_selects_unique_candidate_with_explicit_period_evidence(self):
        for release_period, url in (
            ("Q2 2026", "https://investor.example.com/q2-2026-results.pdf"),
            ("H1 2026", "https://investor.example.com/H1_2026-report.pdf"),
            ("FY 2026", "https://investor.example.com/fy2026-results.pdf"),
        ):
            with self.subTest(release_period=release_period):
                selection = select_results_page_release_candidate(
                    self._event(),
                    (self._candidate(url),),
                    release_period=release_period,
                )
                self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)

    def test_invalid_period_is_rejected_even_when_exact_date_matches(self):
        with self.assertRaises(ValueError):
            select_results_page_release_candidate(
                self._event(),
                (self._candidate("https://investor.example.com/2026-08-26-results.pdf"),),
                release_period="Q5 2026",
            )

    def test_period_evidence_must_exist_within_one_field(self):
        selection = select_results_page_release_candidate(
            self._event(),
            (self._candidate("https://investor.example.com/releases/Q2", "2026 results"),),
            release_period="Q2 2026",
        )
        self.assertEqual(selection.status, ResultsPageSelectionStatus.NO_MATCH)

    def test_rejects_period_labels_embedded_in_larger_alphanumeric_tokens(self):
        for candidate in (
            self._candidate("https://investor.example.com/q2-2026results.pdf"),
            self._candidate("https://investor.example.com/release.pdf", "FY2026annual results"),
        ):
            with self.subTest(candidate=candidate):
                selection = select_results_page_release_candidate(
                    self._event(),
                    (candidate,),
                    release_period="Q2 2026" if "q2" in candidate.source_url else "FY 2026",
                )
                self.assertEqual(selection.status, ResultsPageSelectionStatus.NO_MATCH)

    def test_exact_date_tier_wins_over_period_only_candidate(self):
        exact = self._candidate("https://investor.example.com/2026-08-26-results.pdf")
        period_only = self._candidate("https://investor.example.com/q2-2026-results.pdf")
        selection = select_results_page_release_candidate(
            self._event(),
            (period_only, exact),
            release_period="Q2 2026",
        )
        self.assertEqual(selection.status, ResultsPageSelectionStatus.SELECTED)
        self.assertEqual(selection.candidate, exact)

    def test_period_evidence_fails_closed_when_ambiguous(self):
        selection = select_results_page_release_candidate(
            self._event(),
            (
                self._candidate("https://investor.example.com/q2-2026-report.pdf"),
                self._candidate("https://investor.example.com/q2_2026-release.html"),
            ),
            release_period="Q2 2026",
        )
        self.assertEqual(selection.status, ResultsPageSelectionStatus.AMBIGUOUS)

    def test_rejects_invalid_period_labels_instead_of_guessing(self):
        for release_period in ("Q5 2026", "2026 Q2", "2026", "H3 2026"):
            with self.subTest(release_period=release_period):
                with self.assertRaises(ValueError):
                    select_results_page_release_candidate(
                        self._event(),
                        (self._candidate("https://investor.example.com/q2-2026-results.pdf"),),
                        release_period=release_period,
                    )

    def test_returns_ambiguous_when_multiple_candidates_match(self):
        selection = select_results_page_release_candidate(
            self._event(),
            (
                self._candidate("https://investor.example.com/2026-08-26/report.pdf"),
                self._candidate("https://investor.example.com/2026-08-26/release.html"),
            ),
        )
        self.assertEqual(selection.status, ResultsPageSelectionStatus.AMBIGUOUS)

    def test_ignores_candidates_for_another_event(self):
        selection = select_results_page_release_candidate(
            self._event(),
            (
                self._candidate(
                    "https://investor.example.com/2026-08-26/report.pdf",
                    event_id="calendar:other-event",
                ),
            ),
        )
        self.assertEqual(selection.status, ResultsPageSelectionStatus.NO_MATCH)


if __name__ == "__main__":
    unittest.main()
