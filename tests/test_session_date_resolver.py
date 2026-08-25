from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from trading_system.market_session_profile import SYDNEY_MARKET_SESSION_PROFILE
from trading_system.session_date_resolver import resolve_session_dates


def _close(day: date, hour: int = 6) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


SESSIONS = (
    (date(2026, 8, 20), _close(date(2026, 8, 20))),
    (date(2026, 8, 21), _close(date(2026, 8, 21))),
    (date(2026, 8, 24), _close(date(2026, 8, 24))),
    (date(2026, 8, 25), _close(date(2026, 8, 25))),
)


class SessionDateResolverTests(unittest.TestCase):
    def test_post_close_event_includes_same_day_session(self) -> None:
        resolution = resolve_session_dates(
            datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=SESSIONS,
        )
        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 24))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 21))

    def test_pre_close_event_excludes_same_day_session(self) -> None:
        resolution = resolve_session_dates(
            datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=SESSIONS,
        )
        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 21))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 20))

    def test_event_exactly_at_close_excludes_that_session(self) -> None:
        resolution = resolve_session_dates(
            _close(date(2026, 8, 24)),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=SESSIONS,
        )
        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 21))
        self.assertEqual(resolution.previous_closed_session, date(2026, 8, 20))

    def test_now_gate_rejects_selected_session_that_has_not_closed_yet(self) -> None:
        with self.assertRaisesRegex(ValueError, "has not closed yet"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=SESSIONS,
                now=datetime(2026, 8, 24, 5, 59, tzinfo=UTC),
            )

    def test_non_session_day_resolves_forward(self) -> None:
        resolution = resolve_session_dates(
            datetime(2026, 8, 22, 1, 0, tzinfo=UTC),
            profile=SYDNEY_MARKET_SESSION_PROFILE,
            session_closes=SESSIONS,
        )
        self.assertEqual(resolution.event_trading_date, date(2026, 8, 24))
        self.assertEqual(resolution.latest_closed_session, date(2026, 8, 21))

    def test_rejects_naive_close_and_unsorted_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=((date(2026, 8, 21), datetime(2026, 8, 21, 6)),),
            )
        with self.assertRaisesRegex(ValueError, "strictly ascending"):
            resolve_session_dates(
                datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                profile=SYDNEY_MARKET_SESSION_PROFILE,
                session_closes=(SESSIONS[2], SESSIONS[1], SESSIONS[3]),
            )


if __name__ == "__main__":
    unittest.main()
