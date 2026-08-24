from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from trading_system.market_session_profile import SYDNEY_MARKET_SESSION_PROFILE
from trading_system.session_date_resolver import resolve_session_dates


def _close(day: date, *, hour: int = 6, minute: int = 0) -> datetime:
    """A confirmed XASX-style close: 06:00Z on the session date."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


# Fri 21st, Mon 24th, Tue 25th, Wed 26th - the weekend gap is real XASX shape.
AUGUST_SESSIONS = (
    (date(2026, 8, 20), _close(date(2026, 8, 20))),
    (date(2026, 8, 21), _close(date(2026, 8, 21))),
    (date(2026, 8, 24), _close(date(2026, 8, 24))),
    (date(2026, 8, 25), _close(date(2026, 8, 25))),
    (date(2026, 8, 26), _close(date(2026, 8, 26))),
)


class SessionDateResolverTests(unittest.TestCase):
    def test_same_day_event_after_the_close_includes_that_session(self) -> None:
        # Monday event one hour after Monday's close: Monday's daily candle is
        # complete and is the most recent market state before the event.
        resolution = resolve_session_dates(
            datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=AUGUST_SESSIONS,
        )

        self.assertEqual(resolution.event_trading_date, date(2026, 8, 24))
        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 24))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 21))

    def test_same_day_event_before_the_close_excludes_that_session(self) -> None:
        # Monday event one hour before Monday's close: Monday's candle is still
        # forming, so the pair falls back to Friday/Thursday.
        resolution = resolve_session_dates(
            datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=AUGUST_SESSIONS,
        )

        self.assertEqual(resolution.event_trading_date, date(2026, 8, 24))
        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 21))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 20))

    def test_event_exactly_at_the_close_counts_that_session_as_closed(self) -> None:
        resolution = resolve_session_dates(
            _close(date(2026, 8, 24)),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=AUGUST_SESSIONS,
        )

        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 24))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 21))

    def test_event_on_a_non_session_day_resolves_forward_over_the_weekend(self) -> None:
        # Saturday event: trading date moves to Monday, and the pair is the two
        # sessions that had actually closed by then (Friday/Thursday).
        resolution = resolve_session_dates(
            datetime(2026, 8, 22, 1, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=AUGUST_SESSIONS,
        )

        self.assertEqual(resolution.event_local_date, date(2026, 8, 22))
        self.assertEqual(resolution.event_trading_date, date(2026, 8, 24))
        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 21))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 20))

    def test_holiday_gap_is_skipped_using_the_confirmed_calendar(self) -> None:
        # 2026-12-25 and 2026-12-28 are not XASX sessions. A Monday-28th event
        # resolves forward to the 29th with the 24th/23rd pair behind it.
        sessions = (
            (date(2026, 12, 22), _close(date(2026, 12, 22))),
            (date(2026, 12, 23), _close(date(2026, 12, 23))),
            (date(2026, 12, 24), datetime(2026, 12, 24, 3, 10, tzinfo=UTC)),
            (date(2026, 12, 29), datetime(2026, 12, 29, 5, 0, tzinfo=UTC)),
        )

        resolution = resolve_session_dates(
            datetime(2026, 12, 27, 22, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=sessions,
        )

        self.assertEqual(resolution.event_trading_date, date(2026, 12, 29))
        self.assertEqual(resolution.latest_closed_session, date(2026, 12, 24))
        self.assertEqual(resolution.previous_closed_session, date(2026, 12, 23))

    def test_early_close_day_is_eligible_from_its_real_close_not_a_fixed_time(self) -> None:
        # 2026-12-24 is an XASX early close at 03:10Z. An event at 04:00Z that
        # day is after it, so the early-close session counts - a fixed 05:00Z
        # close inferred from the market timezone would wrongly exclude it.
        sessions = (
            (date(2026, 12, 22), _close(date(2026, 12, 22))),
            (date(2026, 12, 23), _close(date(2026, 12, 23))),
            (date(2026, 12, 24), datetime(2026, 12, 24, 3, 10, tzinfo=UTC)),
            (date(2026, 12, 29), datetime(2026, 12, 29, 5, 0, tzinfo=UTC)),
        )

        after_early_close = resolve_session_dates(
            datetime(2026, 12, 24, 4, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=sessions,
        )
        self.assertEqual(after_early_close.event_trading_date, date(2026, 12, 24))
        self.assertEqual(after_early_close.latest_closed_session, date(2026, 12, 24))
        self.assertEqual(after_early_close.previous_closed_session, date(2026, 12, 23))

        before_early_close = resolve_session_dates(
            datetime(2026, 12, 24, 2, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=sessions,
        )
        self.assertEqual(before_early_close.latest_closed_session, date(2026, 12, 23))
        self.assertEqual(before_early_close.previous_closed_session, date(2026, 12, 22))

    def test_sessions_after_the_event_are_never_eligible(self) -> None:
        resolution = resolve_session_dates(
            datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=AUGUST_SESSIONS,
        )

        self.assertLessEqual(resolution.latest_closed_session, resolution.event_trading_date)
        self.assertNotIn(
            resolution.latest_closed_session, (date(2026, 8, 25), date(2026, 8, 26))
        )

    def test_now_gate_accepts_a_pair_whose_closes_have_passed(self) -> None:
        resolution = resolve_session_dates(
            datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=AUGUST_SESSIONS,
            now=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
        )

        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 24))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 21))

    def test_now_gate_fails_closed_without_substituting_an_older_session(self) -> None:
        # The pair implied by event_at is Monday/Friday, but acquisition is
        # running before Monday's close - it must wait, not silently persist
        # the older Friday/Thursday pair.
        with self.assertRaisesRegex(ValueError, "2026-08-24 has not closed yet"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=AUGUST_SESSIONS,
                now=datetime(2026, 8, 24, 5, 59, tzinfo=UTC),
            )

    def test_now_gate_is_independent_of_event_at_eligibility(self) -> None:
        # Same event_at, two different acquisition instants: eligibility by
        # event_at is unchanged, only whether those sessions have closed yet.
        event_at = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
        ok = resolve_session_dates(
            event_at,
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=AUGUST_SESSIONS,
            now=datetime(2026, 8, 24, 23, 0, tzinfo=UTC),
        )
        ungated = resolve_session_dates(
            event_at,
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=AUGUST_SESSIONS,
        )

        self.assertEqual(ok.latest_closed_session, ungated.latest_closed_session)
        self.assertEqual(ok.previous_closed_session, ungated.previous_closed_session)

    def test_rejects_naive_event_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=AUGUST_SESSIONS,
            )

    def test_rejects_naive_now(self) -> None:
        with self.assertRaisesRegex(ValueError, "now must be timezone-aware"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=AUGUST_SESSIONS,
                now=datetime(2026, 8, 24, 6, 0),
            )

    def test_rejects_naive_session_close(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be timezone-aware"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=(
                    (date(2026, 8, 21), _close(date(2026, 8, 21))),
                    (date(2026, 8, 24), datetime(2026, 8, 24, 6, 0)),
                ),
            )

    def test_rejects_unsorted_or_duplicate_calendar_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "strictly ascending"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=(
                    (date(2026, 8, 24), _close(date(2026, 8, 24))),
                    (date(2026, 8, 21), _close(date(2026, 8, 21))),
                    (date(2026, 8, 25), _close(date(2026, 8, 25))),
                ),
            )

        with self.assertRaisesRegex(ValueError, "duplicates"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=(
                    (date(2026, 8, 21), _close(date(2026, 8, 21))),
                    (date(2026, 8, 21), _close(date(2026, 8, 21))),
                    (date(2026, 8, 25), _close(date(2026, 8, 25))),
                ),
            )

    def test_fails_closed_when_calendar_does_not_cover_required_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "two pre-event sessions"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=(
                    (date(2026, 8, 24), _close(date(2026, 8, 24))),
                    (date(2026, 8, 25), _close(date(2026, 8, 25))),
                ),
            )

        with self.assertRaisesRegex(ValueError, "does not reach"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=(
                    (date(2026, 8, 20), _close(date(2026, 8, 20))),
                    (date(2026, 8, 21), _close(date(2026, 8, 21))),
                    (date(2026, 8, 24), _close(date(2026, 8, 24))),
                ),
            )


if __name__ == "__main__":
    unittest.main()
