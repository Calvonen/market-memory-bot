import unittest
from datetime import UTC, date, datetime

from trading_system.events import compare_event
from trading_system.models import EventActuals, EventExpectation


class EventComparisonTests(unittest.TestCase):
    def test_numeric_actuals_are_compared_without_creating_trade_direction(self) -> None:
        expectation = EventExpectation(
            event_id="hays-fy2026-results",
            instrument="HAS.L",
            event_name="Hays plc FY2026 results",
            scheduled_date=date(2026, 8, 20),
            consensus={"operating_profit_m": 42.0, "net_cash_m": 55.0},
            important_kpis=("operating_profit_m", "net_cash_m", "fy27_guidance"),
        )
        actuals = EventActuals(
            event_id="hays-fy2026-results",
            instrument="HAS.L",
            published_at=datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
            source_url="https://example.invalid/hays-official-release",
            metrics={"operating_profit_m": 40.0, "net_cash_m": 60.0},
            guidance_summary="Example structured guidance text",
            price_reaction_pct=-4.2,
        )

        comparison = compare_event(expectation, actuals)

        operating_profit = next(item for item in comparison.metrics if item.metric == "operating_profit_m")
        self.assertEqual(operating_profit.absolute_delta, -2.0)
        self.assertAlmostEqual(operating_profit.percent_delta, -4.7619047619)
        self.assertEqual(comparison.missing_important_kpis, ("fy27_guidance",))
        self.assertEqual(comparison.price_reaction_pct, -4.2)

    def test_event_identity_mismatch_is_rejected(self) -> None:
        expectation = EventExpectation(
            event_id="one",
            instrument="HAS.L",
            event_name="event",
            scheduled_date=date(2026, 8, 20),
        )
        actuals = EventActuals(
            event_id="two",
            instrument="HAS.L",
            published_at=datetime(2026, 8, 20, tzinfo=UTC),
            source_url="https://example.invalid/release",
        )

        with self.assertRaises(ValueError):
            compare_event(expectation, actuals)


if __name__ == "__main__":
    unittest.main()
