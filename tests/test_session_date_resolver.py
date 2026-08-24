from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from trading_system.market_session_profile import SYDNEY_MARKET_SESSION_PROFILE
from trading_system.session_date_resolver import resolve_session_dates


class SessionDateResolverTests(unittest.TestCase):
    def test_resolves_sydney_local_event_date_and_two_prior_sessions(self) -> None:
        resolution = resolve_session_dates(
            datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            confirmed_session_dates=(
                date(2026, 8, 21),
                date(2026, 8, 24),
                date(2026, 8, 25),
                date(2026, 8, 26),
            ),
        )

        self.assertEqual(resolution.event_local_date, date(2026, 8, 25))
        self.assertEqual(resolution.event_trading_date, date(2026, 8, 25))
        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 24))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 21))

    def test_non_session_local_date_moves_to_next_confirmed_session(self) -> None:
        resolution = resolve_session_dates(
            datetime(2026, 8, 22, 1, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            confirmed_session_dates=(
                date(2026, 8, 20),
                date(2026, 8, 21),
                date(2026, 8, 24),
            ),
        )

        self.assertEqual(resolution.event_local_date, date(2026, 8, 22))
        self.assertEqual(resolution.event_trading_date, date(2026, 8, 24))
        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 21))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 20))

    def test_rejects_naive_event_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=(
                    date(2026, 8, 21),
                    date(2026, 8, 24),
                    date(2026, 8, 25),
                ),
            )

    def test_rejects_unsorted_or_duplicate_calendar_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "strictly ascending"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=(date(2026, 8, 24), date(2026, 8, 21), date(2026, 8, 25)),
            )

        with self.assertRaisesRegex(ValueError, "duplicates"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=(date(2026, 8, 21), date(2026, 8, 21), date(2026, 8, 25)),
            )

    def test_accepts_selected_sessions_once_both_closes_have_passed(self) -> None:
        resolution = resolve_session_dates(
            datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            confirmed_session_dates=(
                date(2026, 8, 21),
                date(2026, 8, 24),
                date(2026, 8, 25),
            ),
            session_closes={
                date(2026, 8, 21): datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
                date(2026, 8, 24): datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
            },
            now=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
        )

        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 24))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 21))

    def test_fails_closed_without_substituting_when_latest_session_is_open(self) -> None:
        with self.assertRaisesRegex(ValueError, "2026-08-24 has not closed yet"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=(
                    date(2026, 8, 20),
                    date(2026, 8, 21),
                    date(2026, 8, 24),
                    date(2026, 8, 25),
                ),
                session_closes={
                    date(2026, 8, 20): datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
                    date(2026, 8, 21): datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
                    date(2026, 8, 24): datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
                },
                now=datetime(2026, 8, 24, 5, 59, tzinfo=UTC),
            )

    def test_close_gate_uses_the_actual_close_not_the_session_date(self) -> None:
        # "now" is on the session date and after midnight local, but before the
        # exchange close - date ordering alone would wrongly accept this.
        with self.assertRaisesRegex(ValueError, "has not closed yet"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=(
                    date(2026, 8, 21),
                    date(2026, 8, 24),
                    date(2026, 8, 25),
                ),
                session_closes={
                    date(2026, 8, 21): datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
                    date(2026, 8, 24): datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
                },
                now=datetime(2026, 8, 24, 3, 0, tzinfo=UTC),
            )

    def test_close_gate_also_rejects_an_unclosed_previous_session(self) -> None:
        with self.assertRaisesRegex(ValueError, "2026-08-21 has not closed yet"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=(
                    date(2026, 8, 21),
                    date(2026, 8, 24),
                    date(2026, 8, 25),
                ),
                session_closes={
                    date(2026, 8, 21): datetime(2026, 8, 26, 6, 0, tzinfo=UTC),
                    date(2026, 8, 24): datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
                },
                now=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
            )

    def test_close_gate_fails_closed_when_a_close_time_is_missing(self) -> None:
        with self.assertRaisesRegex(ValueError, "no close time for session 2026-08-24"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=(
                    date(2026, 8, 21),
                    date(2026, 8, 24),
                    date(2026, 8, 25),
                ),
                session_closes={date(2026, 8, 21): datetime(2026, 8, 21, 6, 0, tzinfo=UTC)},
                now=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
            )

    def test_close_gate_requires_both_inputs_and_an_aware_now(self) -> None:
        sessions = (date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25))
        closes = {
            date(2026, 8, 21): datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
            date(2026, 8, 24): datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
        }

        with self.assertRaisesRegex(ValueError, "supplied together"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=sessions,
                session_closes=closes,
            )

        with self.assertRaisesRegex(ValueError, "now must be timezone-aware"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=sessions,
                session_closes=closes,
                now=datetime(2026, 8, 24, 6, 0),
            )

    def test_fails_closed_when_calendar_does_not_cover_required_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "two pre-event sessions"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=(date(2026, 8, 24), date(2026, 8, 25)),
            )

        with self.assertRaisesRegex(ValueError, "does not reach"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                confirmed_session_dates=(date(2026, 8, 21), date(2026, 8, 24)),
            )


if __name__ == "__main__":
    unittest.main()
