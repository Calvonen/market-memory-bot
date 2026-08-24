from __future__ import annotations

import unittest
from datetime import date

from trading_system.market_session_profile import SYDNEY_MARKET_SESSION_PROFILE
from trading_system.session_calendar_adapter import confirmed_session_dates


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
