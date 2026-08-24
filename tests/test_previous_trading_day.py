from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

import pandas as pd

from trading_system.previous_trading_day import previous_trading_day_snapshot


class PreviousTradingDaySnapshotTests(unittest.TestCase):
    def test_selects_actual_sessions_before_event_date(self):
        frame = pd.DataFrame(
            {"Close": [Decimal("100"), Decimal("103"), Decimal("106.09")]},
            index=pd.to_datetime(["2026-08-20", "2026-08-21", "2026-08-24"]),
        )

        snapshot = previous_trading_day_snapshot(
            frame,
            event_trading_date=date(2026, 8, 24),
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.trading_date, date(2026, 8, 21))
        self.assertEqual(snapshot.prior_trading_date, date(2026, 8, 20))
        self.assertEqual(snapshot.prior_close_price, Decimal("100"))
        self.assertEqual(snapshot.close_price, Decimal("103"))
        self.assertEqual(snapshot.return_pct, Decimal("3.00"))
        self.assertEqual(snapshot.direction, "up")

    def test_ignores_event_day_and_future_rows(self):
        frame = pd.DataFrame(
            {"Close": [Decimal("100"), Decimal("95"), Decimal("120"), Decimal("130")]},
            index=pd.to_datetime(["2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26"]),
        )

        snapshot = previous_trading_day_snapshot(
            frame,
            event_trading_date=date(2026, 8, 25),
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.trading_date, date(2026, 8, 24))
        self.assertEqual(snapshot.prior_trading_date, date(2026, 8, 21))
        self.assertEqual(snapshot.return_pct, Decimal("-5.00"))
        self.assertEqual(snapshot.direction, "down")

    def test_flat_close_is_flat(self):
        frame = pd.DataFrame(
            {"Close": [Decimal("7.25"), Decimal("7.25")]},
            index=pd.to_datetime(["2026-08-20", "2026-08-21"]),
        )

        snapshot = previous_trading_day_snapshot(
            frame,
            event_trading_date=date(2026, 8, 24),
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.return_pct, Decimal("0"))
        self.assertEqual(snapshot.direction, "flat")

    def test_requires_two_completed_sessions(self):
        frame = pd.DataFrame(
            {"Close": [Decimal("100"), Decimal("101")]},
            index=pd.to_datetime(["2026-08-21", "2026-08-24"]),
        )

        snapshot = previous_trading_day_snapshot(
            frame,
            event_trading_date=date(2026, 8, 24),
        )

        self.assertIsNone(snapshot)

    def test_rejects_missing_close_column(self):
        frame = pd.DataFrame(
            {"Open": [Decimal("100"), Decimal("101")]},
            index=pd.to_datetime(["2026-08-20", "2026-08-21"]),
        )

        with self.assertRaisesRegex(ValueError, "missing Close"):
            previous_trading_day_snapshot(
                frame,
                event_trading_date=date(2026, 8, 24),
            )

    def test_rejects_non_positive_or_non_finite_close(self):
        for bad_close in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(bad_close=bad_close):
                frame = pd.DataFrame(
                    {"Close": [Decimal("100"), bad_close]},
                    index=pd.to_datetime(["2026-08-20", "2026-08-21"]),
                )

                with self.assertRaisesRegex(ValueError, "finite positive"):
                    previous_trading_day_snapshot(
                        frame,
                        event_trading_date=date(2026, 8, 24),
                    )

    def test_sorts_unsorted_daily_rows_before_selecting(self):
        frame = pd.DataFrame(
            {"Close": [Decimal("103"), Decimal("100"), Decimal("999")]},
            index=pd.to_datetime(["2026-08-21", "2026-08-20", "2026-08-24"]),
        )

        snapshot = previous_trading_day_snapshot(
            frame,
            event_trading_date=date(2026, 8, 24),
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.prior_close_price, Decimal("100"))
        self.assertEqual(snapshot.close_price, Decimal("103"))
        self.assertEqual(snapshot.return_pct, Decimal("3.00"))


if __name__ == "__main__":
    unittest.main()
