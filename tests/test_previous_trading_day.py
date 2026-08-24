from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd

from trading_system.previous_trading_day import (
    PreEventMarketContext,
    pre_event_market_context,
)


def _frame(rows: list[tuple[str, str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [Decimal(row[1]) for row in rows],
            "High": [Decimal(row[2]) for row in rows],
            "Low": [Decimal(row[3]) for row in rows],
            "Close": [Decimal(row[4]) for row in rows],
        },
        index=pd.to_datetime([row[0] for row in rows]),
    )


class PreEventMarketContextTests(unittest.TestCase):
    def test_builds_previous_session_ohlc_and_close_to_close_context(self):
        frame = _frame(
            [
                ("2026-08-20", "99", "101", "98", "100"),
                ("2026-08-21", "101", "104", "100", "103"),
                ("2026-08-24", "106", "107", "105", "106.5"),
            ]
        )

        context = pre_event_market_context(
            frame,
            event_trading_date=date(2026, 8, 24),
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.session_date, date(2026, 8, 21))
        self.assertEqual(context.previous_session_date, date(2026, 8, 20))
        self.assertEqual(context.open_price, Decimal("101"))
        self.assertEqual(context.high_price, Decimal("104"))
        self.assertEqual(context.low_price, Decimal("100"))
        self.assertEqual(context.close_price, Decimal("103"))
        self.assertEqual(context.previous_close_price, Decimal("100"))
        self.assertEqual(context.close_to_close_return_pct, Decimal("3.00"))
        self.assertEqual(context.close_to_close_direction, "up")

    def test_session_return_is_open_to_close(self):
        frame = _frame(
            [
                ("2026-08-20", "99", "101", "98", "100"),
                ("2026-08-21", "100", "105", "99", "102"),
            ]
        )

        context = pre_event_market_context(
            frame,
            event_trading_date=date(2026, 8, 24),
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.session_return_pct, Decimal("2.00"))
        self.assertEqual(context.close_to_close_return_pct, Decimal("2.00"))

    def test_ignores_event_day_and_future_rows(self):
        frame = _frame(
            [
                ("2026-08-21", "99", "101", "98", "100"),
                ("2026-08-24", "100", "101", "94", "95"),
                ("2026-08-25", "119", "121", "118", "120"),
                ("2026-08-26", "129", "131", "128", "130"),
            ]
        )

        context = pre_event_market_context(
            frame,
            event_trading_date=date(2026, 8, 25),
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.session_date, date(2026, 8, 24))
        self.assertEqual(context.previous_session_date, date(2026, 8, 21))
        self.assertEqual(context.close_to_close_return_pct, Decimal("-5.00"))
        self.assertEqual(context.close_to_close_direction, "down")

    def test_requires_two_completed_sessions(self):
        frame = _frame(
            [
                ("2026-08-21", "99", "101", "98", "100"),
                ("2026-08-24", "100", "102", "99", "101"),
            ]
        )

        self.assertIsNone(
            pre_event_market_context(frame, event_trading_date=date(2026, 8, 24))
        )

    def test_rejects_timezone_aware_index_before_session_selection(self):
        frame = _frame(
            [
                ("2026-08-20", "99", "101", "98", "100"),
                ("2026-08-21", "100", "104", "99", "103"),
            ]
        )
        frame.index = frame.index.tz_localize("Europe/Helsinki").tz_convert("UTC")

        with self.assertRaisesRegex(ValueError, "timezone-naive market session dates"):
            pre_event_market_context(frame, event_trading_date=date(2026, 8, 24))

    def test_rejects_missing_session_timestamps(self):
        frame = _frame(
            [
                ("2026-08-20", "99", "101", "98", "100"),
                ("2026-08-21", "100", "104", "99", "103"),
            ]
        )
        frame.index = pd.DatetimeIndex([frame.index[0], pd.NaT])

        with self.assertRaisesRegex(ValueError, "must not contain missing market session dates"):
            pre_event_market_context(frame, event_trading_date=date(2026, 8, 24))

    def test_rejects_duplicate_session_dates(self):
        frame = _frame(
            [
                ("2026-08-20", "99", "101", "98", "100"),
                ("2026-08-21", "100", "104", "99", "103"),
                ("2026-08-21", "101", "105", "100", "104"),
            ]
        )

        with self.assertRaisesRegex(ValueError, "duplicate market session dates"):
            pre_event_market_context(frame, event_trading_date=date(2026, 8, 24))

    def test_rejects_intraday_index_labels(self):
        frame = _frame(
            [
                ("2026-08-20 00:00", "99", "101", "98", "100"),
                ("2026-08-21 12:00", "100", "104", "99", "103"),
            ]
        )

        with self.assertRaisesRegex(ValueError, "midnight market session dates"):
            pre_event_market_context(frame, event_trading_date=date(2026, 8, 24))

    def test_rejects_datetime_for_session_date_contract(self):
        with self.assertRaisesRegex(ValueError, "session_date must be a date"):
            PreEventMarketContext(
                session_date=datetime(2026, 8, 21, tzinfo=UTC),
                previous_session_date=date(2026, 8, 20),
                open_price=Decimal("100"),
                high_price=Decimal("104"),
                low_price=Decimal("99"),
                close_price=Decimal("103"),
                previous_close_price=Decimal("100"),
                session_return_pct=Decimal("3.00"),
                close_to_close_return_pct=Decimal("3.00"),
            )

    def test_serializes_stable_context_without_late_session_metric(self):
        context = PreEventMarketContext(
            session_date=date(2026, 8, 21),
            previous_session_date=date(2026, 8, 20),
            open_price=Decimal("100"),
            high_price=Decimal("104"),
            low_price=Decimal("99"),
            close_price=Decimal("103"),
            previous_close_price=Decimal("100"),
            session_return_pct=Decimal("3.00"),
            close_to_close_return_pct=Decimal("3.00"),
        )

        self.assertEqual(
            context.to_dict(),
            {
                "schema_version": 1,
                "session_date": "2026-08-21",
                "previous_session_date": "2026-08-20",
                "open_price": "100",
                "high_price": "104",
                "low_price": "99",
                "close_price": "103",
                "previous_close_price": "100",
                "session_return_pct": "3.00",
                "close_to_close_return_pct": "3.00",
                "close_to_close_direction": "up",
            },
        )


if __name__ == "__main__":
    unittest.main()
