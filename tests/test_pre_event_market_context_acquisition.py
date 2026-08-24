from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

import pandas as pd

from trading_system.pre_event_market_context_acquisition import (
    acquire_pre_event_market_context,
)


class PreEventMarketContextAcquisitionTests(unittest.TestCase):
    def test_fetches_daily_history_and_builds_context(self) -> None:
        calls: list[tuple[str, str, str]] = []

        def fetcher(ticker: str, period: str, interval: str) -> pd.DataFrame:
            calls.append((ticker, period, interval))
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

        context = acquire_pre_event_market_context(
            ticker=" nhf.asx ",
            event_trading_date=date(2026, 8, 24),
            last_confirmed_closed_session_date=date(2026, 8, 21),
            fetcher=fetcher,
        )

        self.assertEqual(calls, [("NHF.ASX", "1mo", "1d")])
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.session_date, date(2026, 8, 21))
        self.assertEqual(context.previous_session_date, date(2026, 8, 20))
        self.assertEqual(context.close_price, Decimal("103"))
        self.assertEqual(context.previous_close_price, Decimal("100"))
        self.assertEqual(context.close_to_close_return_pct, Decimal("3.00"))

    def test_excludes_still_forming_daily_bar(self) -> None:
        def fetcher(ticker: str, period: str, interval: str) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "Open": [Decimal("99"), Decimal("101"), Decimal("150")],
                    "High": [Decimal("101"), Decimal("104"), Decimal("170")],
                    "Low": [Decimal("98"), Decimal("100"), Decimal("140")],
                    "Close": [Decimal("100"), Decimal("103"), Decimal("165")],
                    "Volume": [1000, 1200, 100],
                },
                index=pd.to_datetime(["2026-08-20", "2026-08-21", "2026-08-24"]),
            )

        context = acquire_pre_event_market_context(
            ticker="NHF.ASX",
            event_trading_date=date(2026, 8, 25),
            last_confirmed_closed_session_date=date(2026, 8, 21),
            fetcher=fetcher,
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.session_date, date(2026, 8, 21))
        self.assertEqual(context.close_price, Decimal("103"))
        self.assertEqual(context.previous_close_price, Decimal("100"))

    def test_rejects_when_confirmed_closed_session_is_missing(self) -> None:
        def fetcher(ticker: str, period: str, interval: str) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "Open": [Decimal("98"), Decimal("99"), Decimal("150")],
                    "High": [Decimal("100"), Decimal("101"), Decimal("170")],
                    "Low": [Decimal("97"), Decimal("98"), Decimal("140")],
                    "Close": [Decimal("99"), Decimal("100"), Decimal("165")],
                    "Volume": [1000, 1200, 100],
                },
                index=pd.to_datetime(["2026-08-19", "2026-08-20", "2026-08-24"]),
            )

        with self.assertRaisesRegex(ValueError, "confirmed closed session data is missing"):
            acquire_pre_event_market_context(
                ticker="NHF.ASX",
                event_trading_date=date(2026, 8, 25),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                fetcher=fetcher,
            )

    def test_rejects_missing_confirmed_session_before_none_for_short_history(self) -> None:
        def fetcher(ticker: str, period: str, interval: str) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "Open": [Decimal("99"), Decimal("150")],
                    "High": [Decimal("101"), Decimal("170")],
                    "Low": [Decimal("98"), Decimal("140")],
                    "Close": [Decimal("100"), Decimal("165")],
                    "Volume": [1200, 100],
                },
                index=pd.to_datetime(["2026-08-20", "2026-08-24"]),
            )

        with self.assertRaisesRegex(ValueError, "confirmed closed session data is missing"):
            acquire_pre_event_market_context(
                ticker="NHF.ASX",
                event_trading_date=date(2026, 8, 25),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                fetcher=fetcher,
            )

    def test_returns_none_when_two_confirmed_sessions_are_not_available(self) -> None:
        def fetcher(ticker: str, period: str, interval: str) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "Open": [Decimal("100"), Decimal("101")],
                    "High": [Decimal("102"), Decimal("103")],
                    "Low": [Decimal("99"), Decimal("100")],
                    "Close": [Decimal("101"), Decimal("102")],
                    "Volume": [1000, 1200],
                },
                index=pd.to_datetime(["2026-08-21", "2026-08-24"]),
            )

        self.assertIsNone(
            acquire_pre_event_market_context(
                ticker="DKS",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                fetcher=fetcher,
            )
        )

    def test_rejects_blank_ticker_before_fetch(self) -> None:
        called = False

        def fetcher(ticker: str, period: str, interval: str) -> pd.DataFrame:
            nonlocal called
            called = True
            raise AssertionError("fetcher must not be called")

        with self.assertRaisesRegex(ValueError, "ticker is required"):
            acquire_pre_event_market_context(
                ticker="   ",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                fetcher=fetcher,
            )
        self.assertFalse(called)

    def test_selector_validation_is_not_bypassed(self) -> None:
        def fetcher(ticker: str, period: str, interval: str) -> pd.DataFrame:
            frame = pd.DataFrame(
                {
                    "Open": [Decimal("100"), Decimal("101")],
                    "High": [Decimal("102"), Decimal("103")],
                    "Low": [Decimal("99"), Decimal("100")],
                    "Close": [Decimal("101"), Decimal("102")],
                    "Volume": [1000, 1200],
                },
                index=pd.to_datetime(["2026-08-20", "2026-08-21"]),
            )
            frame.index = frame.index.tz_localize("UTC")
            return frame

        with self.assertRaisesRegex(ValueError, "timezone-naive market session dates"):
            acquire_pre_event_market_context(
                ticker="DKS",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                fetcher=fetcher,
            )


if __name__ == "__main__":
    unittest.main()
