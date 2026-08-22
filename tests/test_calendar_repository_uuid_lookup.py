import unittest
from dataclasses import replace
from datetime import date
from uuid import UUID

from trading_system.calendar_provider import CalendarCandidate
from trading_system.calendar_repository import CalendarEventStatus, InMemoryCalendarEventRepository


def _candidate() -> CalendarCandidate:
    return CalendarCandidate(
        company_name="Apple Inc",
        instrument="AAPL",
        market="NASDAQ",
        event_type="earnings",
        scheduled_date=date(2026, 10, 29),
        source="finnhub",
        occurrence_key="2026Q4",
    )


class InMemoryCalendarUuidLookupTests(unittest.TestCase):
    def _repo_with_candidate(self) -> tuple[InMemoryCalendarEventRepository, str, str]:
        repo = InMemoryCalendarEventRepository()
        dashless = repo.sync_candidates([_candidate()], source="finnhub").inserted[0]
        dashed = str(UUID(dashless))
        return repo, dashless, dashed

    def test_track_accepts_dashed_input_for_dashless_stored_key(self) -> None:
        repo, dashless, dashed = self._repo_with_candidate()

        tracked = repo.track(dashed)

        self.assertEqual(tracked.status, CalendarEventStatus.TRACKED)
        self.assertIn(dashless, repo.events)
        self.assertNotIn(dashed, repo.events)

    def test_untrack_accepts_dashed_input_for_dashless_stored_key(self) -> None:
        repo, dashless, dashed = self._repo_with_candidate()
        repo.track(dashless)

        untracked = repo.untrack(dashed)

        self.assertEqual(untracked.status, CalendarEventStatus.CANDIDATE)
        self.assertIn(dashless, repo.events)
        self.assertNotIn(dashed, repo.events)

    def test_track_accepts_dashless_input_for_dashed_stored_key(self) -> None:
        repo, dashless, dashed = self._repo_with_candidate()
        event = repo.events.pop(dashless)
        repo.events[dashed] = replace(event, calendar_event_id=dashed)

        tracked = repo.track(dashless)

        self.assertEqual(tracked.status, CalendarEventStatus.TRACKED)
        self.assertIn(dashed, repo.events)
        self.assertNotIn(dashless, repo.events)

    def test_untrack_accepts_dashless_input_for_dashed_stored_key(self) -> None:
        repo, dashless, dashed = self._repo_with_candidate()
        event = repo.events.pop(dashless)
        repo.events[dashed] = replace(
            event,
            calendar_event_id=dashed,
            status=CalendarEventStatus.TRACKED,
        )

        untracked = repo.untrack(dashless)

        self.assertEqual(untracked.status, CalendarEventStatus.CANDIDATE)
        self.assertIn(dashed, repo.events)
        self.assertNotIn(dashless, repo.events)


if __name__ == "__main__":
    unittest.main()
