from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from trading_system.market_session_profile import SYDNEY_MARKET_SESSION_PROFILE
from trading_system.session_calendar_adapter import (
    confirmed_session_closes,
    confirmed_session_dates,
)


class SessionCalendarAdapterTests(unittest.TestCase):
    def test_xasx_returns_confirmed_august_2026_sessions(self) -> None:
        sessions = confirmed_session_dates(
            SYDNEY_MARKET_SESSION_PROFILE,
            start_date=date(2026, 8, 21),
            end_date=date(2026, 8, 25),
        )

        self.assertEqual(
            sessions,
            (
                date(2026, 8, 21),
                date(2026, 8, 24),
                date(2026, 8, 25),
            ),
        )

    def test_xasx_excludes_christmas_exchange_holiday(self) -> None:
        sessions = confirmed_session_dates(
            SYDNEY_MARKET_SESSION_PROFILE,
            start_date=date(2026, 12, 24),
            end_date=date(2026, 12, 29),
        )

        self.assertEqual(
            sessions,
            (
                date(2026, 12, 24),
                date(2026, 12, 29),
            ),
        )

    def test_xasx_returns_real_utc_session_closes(self) -> None:
        closes = confirmed_session_closes(
            SYDNEY_MARKET_SESSION_PROFILE,
            start_date=date(2026, 8, 21),
            end_date=date(2026, 8, 25),
        )

        self.assertEqual(
            closes,
            (
                (date(2026, 8, 21), datetime(2026, 8, 21, 6, 0, tzinfo=UTC)),
                (date(2026, 8, 24), datetime(2026, 8, 24, 6, 0, tzinfo=UTC)),
                (date(2026, 8, 25), datetime(2026, 8, 25, 6, 0, tzinfo=UTC)),
            ),
        )

    def test_xasx_reports_early_close_and_dst_shifted_close(self) -> None:
        # 2026-12-24 is an XASX early close and Sydney is on DST in December, so
        # neither close can be reconstructed from the market timezone plus a
        # fixed session length - the calendar has to supply them.
        closes = dict(
            confirmed_session_closes(
                SYDNEY_MARKET_SESSION_PROFILE,
                start_date=date(2026, 12, 24),
                end_date=date(2026, 12, 29),
            )
        )

        self.assertEqual(closes[date(2026, 12, 24)], datetime(2026, 12, 24, 3, 10, tzinfo=UTC))
        self.assertEqual(closes[date(2026, 12, 29)], datetime(2026, 12, 29, 5, 0, tzinfo=UTC))
        self.assertNotIn(date(2026, 12, 25), closes)

    def test_session_closes_rejects_reversed_date_range_before_loading_calendar(self) -> None:
        called = False

        def loader(_calendar_id: str):
            nonlocal called
            called = True
            raise AssertionError("loader must not be called")

        with self.assertRaisesRegex(ValueError, "start_date"):
            confirmed_session_closes(
                SYDNEY_MARKET_SESSION_PROFILE,
                start_date=date(2026, 8, 25),
                end_date=date(2026, 8, 24),
                calendar_loader=loader,
            )

        self.assertFalse(called)

    def test_rejects_reversed_date_range_before_loading_calendar(self) -> None:
        called = False

        def loader(_calendar_id: str):
            nonlocal called
            called = True
            raise AssertionError("loader must not be called")

        with self.assertRaisesRegex(ValueError, "start_date"):
            confirmed_session_dates(
                SYDNEY_MARKET_SESSION_PROFILE,
                start_date=date(2026, 8, 25),
                end_date=date(2026, 8, 24),
                calendar_loader=loader,
            )

        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
