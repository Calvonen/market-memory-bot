from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

import pandas as pd

from trading_system.pre_event_market_context_acquisition import acquire_pre_event_market_context


class PreEventMarketContextAcquisitionTests(unittest.TestCase):
    @staticmethod
    def _frame() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Open": [Decimal("99"), Decimal("101"), Decimal("106")],
                "High": [Decimal("101"), Decimal("104"), Decimal("108")],
                "Low": [Decimal("98"), Decimal("100"), Decimal("105")],
                "Close": [Decimal("100"), Decimal("103"), Decimal("107")],
                "Volume": [1000, 1200, 900],
            },
            index=pd.to_datetime(["2026-08-20", "2026-08-21", "2026-08-24"]),
        )

    def test_fetches_provider_symbol_and_builds_context(self) -> None:
        calls = []

        def fetcher(ticker, period, interval):
            calls.append((ticker, period, interval))
            return self._frame()

        context = acquire_pre_event_market_context(
            provider_symbol=" wds.ax ",
            event_trading_date=date(2026, 8, 24),
            last_confirmed_closed_session_date=date(2026, 8, 21),
            previous_confirmed_closed_session_date=date(2026, 8, 20),
            fetcher=fetcher,
        )

        self.assertEqual(calls, [("WDS.AX", "1mo", "1d")])
        self.assertEqual(context.session_date, date(2026, 8, 21))
        self.assertEqual(context.previous_session_date, date(2026, 8, 20))

    def test_same_day_closed_session_is_allowed(self) -> None:
        context = acquire_pre_event_market_context(
            provider_symbol="WDS.AX",
            event_trading_date=date(2026, 8, 24),
            last_confirmed_closed_session_date=date(2026, 8, 24),
            previous_confirmed_closed_session_date=date(2026, 8, 21),
            fetcher=lambda *_: self._frame(),
        )
        self.assertEqual(context.session_date, date(2026, 8, 24))
        self.assertEqual(context.previous_session_date, date(2026, 8, 21))

    def test_rejects_session_after_event_trading_date_before_fetch(self) -> None:
        called = False

        def fetcher(*_):
            nonlocal called
            called = True
            return self._frame()

        with self.assertRaisesRegex(ValueError, "must not be after"):
            acquire_pre_event_market_context(
                provider_symbol="WDS.AX",
                event_trading_date=date(2026, 8, 21),
                last_confirmed_closed_session_date=date(2026, 8, 24),
                previous_confirmed_closed_session_date=date(2026, 8, 21),
                fetcher=fetcher,
            )
        self.assertFalse(called)

    def test_rejects_invalid_session_order_before_fetch(self) -> None:
        called = False

        def fetcher(*_):
            nonlocal called
            called = True
            return self._frame()

        with self.assertRaisesRegex(ValueError, "previous confirmed session must precede"):
            acquire_pre_event_market_context(
                provider_symbol="WDS.AX",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                previous_confirmed_closed_session_date=date(2026, 8, 21),
                fetcher=fetcher,
            )
        self.assertFalse(called)

    def test_rejects_blank_provider_symbol_before_fetch(self) -> None:
        called = False

        def fetcher(*_):
            nonlocal called
            called = True
            return self._frame()

        with self.assertRaisesRegex(ValueError, "provider_symbol is required"):
            acquire_pre_event_market_context(
                provider_symbol="   ",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                previous_confirmed_closed_session_date=date(2026, 8, 20),
                fetcher=fetcher,
            )
        self.assertFalse(called)

    def test_missing_required_session_fails_closed(self) -> None:
        frame = self._frame().drop(pd.Timestamp("2026-08-21"))
        with self.assertRaisesRegex(ValueError, "confirmed closed session data is missing"):
            acquire_pre_event_market_context(
                provider_symbol="WDS.AX",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                previous_confirmed_closed_session_date=date(2026, 8, 20),
                fetcher=lambda *_: frame,
            )

    def test_timezone_aware_daily_index_is_rejected(self) -> None:
        frame = self._frame()
        frame.index = frame.index.tz_localize("UTC")
        with self.assertRaisesRegex(ValueError, "timezone-naive market session dates"):
            acquire_pre_event_market_context(
                provider_symbol="WDS.AX",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                previous_confirmed_closed_session_date=date(2026, 8, 20),
                fetcher=lambda *_: frame,
            )


if __name__ == "__main__":
    unittest.main()
