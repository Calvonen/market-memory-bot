from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from trading_system.pre_event_market_context import PreEventMarketContext


class PreEventMarketContextTests(unittest.TestCase):
    def _context(self, **overrides) -> PreEventMarketContext:
        values = {
            "session_date": date(2026, 8, 21),
            "open_price": Decimal("6.18"),
            "high_price": Decimal("6.34"),
            "low_price": Decimal("6.12"),
            "close_price": Decimal("6.30"),
            "session_return_pct": Decimal("1.941747572815533980582524300"),
            "late_session_return_pct": Decimal("0.72"),
        }
        values.update(overrides)
        return PreEventMarketContext(**values)

    def test_previous_session_close_is_closed_market_reference(self) -> None:
        context = self._context()

        self.assertEqual(context.reference_price, Decimal("6.30"))

    def test_serializes_stable_versioned_snapshot(self) -> None:
        context = self._context()

        self.assertEqual(
            context.to_dict(),
            {
                "schema_version": 1,
                "session_date": "2026-08-21",
                "open_price": "6.18",
                "high_price": "6.34",
                "low_price": "6.12",
                "close_price": "6.30",
                "session_return_pct": "1.941747572815533980582524300",
                "late_session_return_pct": "0.72",
            },
        )

    def test_late_session_return_is_optional(self) -> None:
        context = self._context(late_session_return_pct=None)

        self.assertIsNone(context.late_session_return_pct)
        self.assertIsNone(context.to_dict()["late_session_return_pct"])

    def test_rejects_non_positive_or_non_finite_prices(self) -> None:
        for field in ("open_price", "high_price", "low_price", "close_price"):
            for value in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(
                    ValueError, "finite and positive"
                ):
                    self._context(**{field: value})

    def test_rejects_inconsistent_ohlc(self) -> None:
        with self.assertRaisesRegex(ValueError, "high_price"):
            self._context(high_price=Decimal("6.20"))
        with self.assertRaisesRegex(ValueError, "low_price"):
            self._context(low_price=Decimal("6.25"))

    def test_rejects_return_that_does_not_match_open_close(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            self._context(session_return_pct=Decimal("9.99"))

    def test_rejects_non_finite_trend_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "session_return_pct must be finite"):
            self._context(session_return_pct=Decimal("NaN"))
        with self.assertRaisesRegex(ValueError, "late_session_return_pct must be finite"):
            self._context(late_session_return_pct=Decimal("Infinity"))


if __name__ == "__main__":
    unittest.main()
