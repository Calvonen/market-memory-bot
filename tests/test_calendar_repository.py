import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date

from trading_system.calendar_provider import CalendarCandidate
from trading_system.calendar_repository import (
    CalendarEventNotFound,
    CalendarEventStatus,
    InMemoryCalendarEventRepository,
    InvalidCalendarEventTransition,
)


def _candidate(**overrides) -> CalendarCandidate:
    defaults = dict(
        company_name="Apple Inc",
        instrument="AAPL",
        market="NASDAQ",
        event_type="earnings",
        scheduled_date=date(2026, 10, 29),
        source="finnhub",
        occurrence_key="2026Q4",
    )
    defaults.update(overrides)
    return CalendarCandidate(**defaults)


class CalendarRepositorySyncTests(unittest.TestCase):
    def test_first_sync_inserts_a_new_candidate(self) -> None:
        repo = InMemoryCalendarEventRepository()

        result = repo.sync_candidates([_candidate()], source="finnhub")

        self.assertEqual(len(result.inserted), 1)
        self.assertEqual(result.updated, ())
        self.assertEqual(result.skipped_locked, ())
        stored = repo.get(result.inserted[0])
        assert stored is not None
        self.assertEqual(stored.status, CalendarEventStatus.CANDIDATE)
        self.assertEqual(stored.instrument, "AAPL")

    # -- idempotent sync / duplicate prevention ----------------------------

    def test_syncing_the_same_candidate_twice_does_not_duplicate(self) -> None:
        repo = InMemoryCalendarEventRepository()

        repo.sync_candidates([_candidate()], source="finnhub")
        second = repo.sync_candidates([_candidate()], source="finnhub")

        self.assertEqual(second.inserted, ())
        all_events = repo.list_upcoming(date(2026, 1, 1), date(2027, 1, 1))
        self.assertEqual(len(all_events), 1)

    def test_repeated_sync_runs_never_grow_the_store(self) -> None:
        repo = InMemoryCalendarEventRepository()
        for _ in range(5):
            repo.sync_candidates([_candidate()], source="finnhub")

        all_events = repo.list_upcoming(date(2026, 1, 1), date(2027, 1, 1))
        self.assertEqual(len(all_events), 1)

    def test_a_still_candidate_events_date_is_updated_by_a_later_sync(self) -> None:
        repo = InMemoryCalendarEventRepository()
        repo.sync_candidates([_candidate(scheduled_date=date(2026, 10, 29))], source="finnhub")

        result = repo.sync_candidates([_candidate(scheduled_date=date(2026, 11, 5))], source="finnhub")

        self.assertEqual(len(result.updated), 1)
        updated = repo.get(result.updated[0])
        assert updated is not None
        self.assertEqual(updated.scheduled_date, date(2026, 11, 5))
        self.assertEqual(updated.status, CalendarEventStatus.CANDIDATE)

    # -- candidate -> tracked -----------------------------------------------

    def test_track_promotes_a_candidate_to_tracked(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted = repo.sync_candidates([_candidate()], source="finnhub").inserted[0]

        tracked = repo.track(inserted)

        self.assertEqual(tracked.status, CalendarEventStatus.TRACKED)
        self.assertEqual(repo.get(inserted).status, CalendarEventStatus.TRACKED)

    def test_tracking_an_already_tracked_event_is_a_no_op(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted = repo.sync_candidates([_candidate()], source="finnhub").inserted[0]
        repo.track(inserted)

        tracked_again = repo.track(inserted)

        self.assertEqual(tracked_again.status, CalendarEventStatus.TRACKED)

    def test_tracking_an_unknown_event_raises_not_found(self) -> None:
        repo = InMemoryCalendarEventRepository()

        with self.assertRaises(CalendarEventNotFound):
            repo.track("does-not-exist")

    # -- tracked -> candidate/untracked --------------------------------------

    def test_untrack_demotes_a_tracked_event_back_to_candidate(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted = repo.sync_candidates([_candidate()], source="finnhub").inserted[0]
        repo.track(inserted)

        untracked = repo.untrack(inserted)

        self.assertEqual(untracked.status, CalendarEventStatus.CANDIDATE)

    def test_untracking_a_candidate_is_a_no_op(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted = repo.sync_candidates([_candidate()], source="finnhub").inserted[0]

        result = repo.untrack(inserted)

        self.assertEqual(result.status, CalendarEventStatus.CANDIDATE)

    def test_untracking_a_further_lifecycle_stage_is_rejected(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted = repo.sync_candidates([_candidate()], source="finnhub").inserted[0]
        # Simulate a future-phase event that has moved beyond tracked -
        # untrack() must refuse to move it, not silently reset it.
        repo.events[inserted] = replace(repo.events[inserted], status=CalendarEventStatus.RESEARCH)

        with self.assertRaises(InvalidCalendarEventTransition):
            repo.untrack(inserted)

    # -- sync must never regress a tracked event back to candidate ---------

    def test_sync_never_drops_a_tracked_event_back_to_candidate(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted = repo.sync_candidates([_candidate()], source="finnhub").inserted[0]
        repo.track(inserted)

        repo.sync_candidates([_candidate()], source="finnhub")

        self.assertEqual(repo.get(inserted).status, CalendarEventStatus.TRACKED)

    # -- tracked event's critical fields are never silently overwritten ----

    def test_sync_does_not_silently_overwrite_a_tracked_events_date(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted = repo.sync_candidates(
            [_candidate(scheduled_date=date(2026, 10, 29))], source="finnhub"
        ).inserted[0]
        repo.track(inserted)

        result = repo.sync_candidates(
            [_candidate(scheduled_date=date(2026, 11, 12))], source="finnhub"
        )

        self.assertEqual(result.skipped_locked, (inserted,))
        self.assertEqual(result.updated, ())
        still_tracked = repo.get(inserted)
        self.assertEqual(still_tracked.scheduled_date, date(2026, 10, 29))
        self.assertEqual(still_tracked.status, CalendarEventStatus.TRACKED)

    def test_sync_does_not_overwrite_a_tracked_events_company_name_or_market(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted = repo.sync_candidates([_candidate()], source="finnhub").inserted[0]
        repo.track(inserted)

        repo.sync_candidates(
            [_candidate(company_name="Renamed Inc", market="NYSE")], source="finnhub"
        )

        still_tracked = repo.get(inserted)
        self.assertEqual(still_tracked.company_name, "Apple Inc")
        self.assertEqual(still_tracked.market, "NASDAQ")

    # -- occurrence identity (P1 fix): recurring events -----------------

    def test_q3_and_q4_earnings_of_the_same_company_remain_two_distinct_events(self) -> None:
        repo = InMemoryCalendarEventRepository()

        result = repo.sync_candidates(
            [
                _candidate(scheduled_date=date(2026, 7, 30), occurrence_key="2026Q3"),
                _candidate(scheduled_date=date(2026, 10, 29), occurrence_key="2026Q4"),
            ],
            source="finnhub",
        )

        self.assertEqual(len(result.inserted), 2)
        all_events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(len(all_events), 2)
        self.assertEqual(
            {e.occurrence_key for e in all_events}, {"2026Q3", "2026Q4"}
        )

    def test_q3_date_shift_updates_the_same_candidate_not_a_duplicate(self) -> None:
        repo = InMemoryCalendarEventRepository()
        repo.sync_candidates(
            [_candidate(scheduled_date=date(2026, 7, 30), occurrence_key="2026Q3")],
            source="finnhub",
        )

        result = repo.sync_candidates(
            [_candidate(scheduled_date=date(2026, 8, 6), occurrence_key="2026Q3")],
            source="finnhub",
        )

        self.assertEqual(result.inserted, ())
        self.assertEqual(len(result.updated), 1)
        all_events = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(len(all_events), 1)
        self.assertEqual(all_events[0].scheduled_date, date(2026, 8, 6))
        self.assertEqual(all_events[0].occurrence_key, "2026Q3")

    def test_tracked_q3_does_not_block_q4_candidate_creation(self) -> None:
        repo = InMemoryCalendarEventRepository()
        q3_id = repo.sync_candidates(
            [_candidate(scheduled_date=date(2026, 7, 30), occurrence_key="2026Q3")],
            source="finnhub",
        ).inserted[0]
        repo.track(q3_id)

        result = repo.sync_candidates(
            [
                _candidate(scheduled_date=date(2026, 7, 30), occurrence_key="2026Q3"),
                _candidate(scheduled_date=date(2026, 10, 29), occurrence_key="2026Q4"),
            ],
            source="finnhub",
        )

        # Q3 (already tracked) is neither duplicated nor overwritten; Q4 is
        # inserted as its own brand-new candidate, unaffected by Q3's status.
        self.assertEqual(result.skipped_locked, (q3_id,))
        self.assertEqual(len(result.inserted), 1)
        q4 = repo.get(result.inserted[0])
        assert q4 is not None
        self.assertEqual(q4.occurrence_key, "2026Q4")
        self.assertEqual(q4.status, CalendarEventStatus.CANDIDATE)
        self.assertEqual(repo.get(q3_id).status, CalendarEventStatus.TRACKED)

    # -- list_upcoming ---------------------------------------------------

    def test_list_upcoming_only_returns_candidate_and_tracked_within_range(self) -> None:
        repo = InMemoryCalendarEventRepository()
        repo.sync_candidates(
            [
                _candidate(instrument="AAPL", scheduled_date=date(2026, 9, 1)),
                _candidate(instrument="MSFT", scheduled_date=date(2027, 6, 1)),
            ],
            source="finnhub",
        )

        upcoming = repo.list_upcoming(date(2026, 1, 1), date(2026, 12, 31))

        self.assertEqual([e.instrument for e in upcoming], ["AAPL"])

    # -- manual event entry (architecture kept, not just earnings) ---------

    def test_manual_event_supports_a_non_earnings_event_type(self) -> None:
        repo = InMemoryCalendarEventRepository()
        candidate = _candidate(
            company_name="Afarak Group",
            instrument="AFAGR.HE",
            market="Suomi",
            event_type="production_report",
            scheduled_date=date(2026, 10, 15),
            source="manual",
        )

        saved = repo.add_manual_event(candidate)

        self.assertEqual(saved.event_type, "production_report")
        self.assertEqual(saved.status, CalendarEventStatus.CANDIDATE)

    def test_manual_event_can_be_added_directly_as_tracked(self) -> None:
        repo = InMemoryCalendarEventRepository()
        candidate = _candidate(instrument="AFAGR.HE", event_type="production_report", source="manual")

        saved = repo.add_manual_event(candidate, status=CalendarEventStatus.TRACKED)

        self.assertEqual(saved.status, CalendarEventStatus.TRACKED)

    def test_manual_event_is_idempotent_against_the_same_identity(self) -> None:
        repo = InMemoryCalendarEventRepository()
        candidate = _candidate(instrument="AFAGR.HE", event_type="production_report", source="manual")

        first = repo.add_manual_event(candidate)
        second = repo.add_manual_event(candidate)

        self.assertEqual(first.calendar_event_id, second.calendar_event_id)

    # -- concurrency regression (P2): first-insert race ---------------------

    def test_concurrent_first_syncs_of_the_same_new_candidate_never_duplicate_or_error(self) -> None:
        # InMemoryCalendarEventRepository serializes sync_candidates() calls
        # behind one lock (unlike the Supabase RPC, which relies on an
        # atomic INSERT ... ON CONFLICT for this same guarantee - see
        # upsert_calendar_candidate() in the migration). This is a
        # defense-in-depth regression proof that racing callers never
        # observe a duplicate row or a raised exception, whichever
        # implementation is under test.
        repo = InMemoryCalendarEventRepository()
        candidate = _candidate()

        def sync_once() -> int:
            result = repo.sync_candidates([candidate], source="finnhub")
            return len(result.inserted)

        with ThreadPoolExecutor(max_workers=8) as pool:
            insert_counts = list(pool.map(lambda _: sync_once(), range(8)))

        # Exactly one of the eight racing calls actually inserted the row;
        # every other call observed it as already existing (update/no-op),
        # never raised, and never created a second row.
        self.assertEqual(sum(insert_counts), 1)
        all_events = repo.list_upcoming(date(2026, 1, 1), date(2027, 1, 1))
        self.assertEqual(len(all_events), 1)


if __name__ == "__main__":
    unittest.main()
