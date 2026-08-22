import unittest
from datetime import datetime, timezone
from decimal import Decimal

from trading_system.market_reaction import (
    MarketReactionEngine,
    ReactionDirection,
)
from trading_system.tracked_candle_pipeline import TrackedMarketCandle


def _candle(
    *,
    close: str = "101",
    interval_minutes: int = 5,
    source_minutes: int = 5,
) -> TrackedMarketCandle:
    value = Decimal(close)
    return TrackedMarketCandle(
        tracked_instrument_id="tracked-1",
        instrument="NOKIA.HE",
        market="Helsinki",
        etoro_instrument_id=4242,
        interval_minutes=interval_minutes,
        start=datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc),
        open=value,
        high=value,
        low=value,
        close=value,
        source_minutes=source_minutes,
    )


class MarketReactionEngineTests(unittest.TestCase):
    def test_positive_reaction_preserves_identity_and_signed_return(self) -> None:
        result = MarketReactionEngine().analyze(
            _candle(close="102.5"),
            reference_price=Decimal("100"),
        )

        self.assertEqual(result.tracked_instrument_id, "tracked-1")
        self.assertEqual(result.instrument, "NOKIA.HE")
        self.assertEqual(result.market, "Helsinki")
        self.assertEqual(result.etoro_instrument_id, 4242)
        self.assertEqual(result.interval_minutes, 5)
        self.assertEqual(result.return_pct, Decimal("2.500"))
        self.assertEqual(result.direction, ReactionDirection.POSITIVE)
        self.assertTrue(result.complete_window)

    def test_negative_reaction_is_information_not_rejection(self) -> None:
        result = MarketReactionEngine().analyze(
            _candle(close="96"),
            reference_price=Decimal("100"),
        )

        self.assertEqual(result.return_pct, Decimal("-4.00"))
        self.assertEqual(result.direction, ReactionDirection.NEGATIVE)

    def test_threshold_band_and_exact_boundaries_are_flat(self) -> None:
        engine = MarketReactionEngine(flat_threshold_pct=Decimal("0.25"))

        for close in ("99.75", "100", "100.25"):
            with self.subTest(close=close):
                result = engine.analyze(_candle(close=close), reference_price=Decimal("100"))
                self.assertEqual(result.direction, ReactionDirection.FLAT)

    def test_sparse_multi_minute_window_is_flagged_but_still_measured(self) -> None:
        result = MarketReactionEngine().analyze(
            _candle(close="101", interval_minutes=15, source_minutes=11),
            reference_price=Decimal("100"),
        )

        self.assertEqual(result.source_minutes, 11)
        self.assertFalse(result.complete_window)
        self.assertEqual(result.return_pct, Decimal("1.00"))

    def test_one_minute_window_is_complete_with_one_source_minute(self) -> None:
        result = MarketReactionEngine().analyze(
            _candle(close="100", interval_minutes=1, source_minutes=1),
            reference_price=Decimal("100"),
        )

        self.assertTrue(result.complete_window)

    def test_invalid_reference_prices_fail_closed(self) -> None:
        engine = MarketReactionEngine()

        for reference in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    engine.analyze(_candle(), reference_price=reference)

    def test_invalid_close_prices_fail_closed(self) -> None:
        engine = MarketReactionEngine()

        for close in ("0", "-1"):
            with self.subTest(close=close):
                with self.assertRaises(ValueError):
                    engine.analyze(_candle(close=close), reference_price=Decimal("100"))

    def test_invalid_threshold_and_source_minute_metadata_fail_closed(self) -> None:
        for threshold in (Decimal("-0.1"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(threshold=threshold):
                with self.assertRaises(ValueError):
                    MarketReactionEngine(flat_threshold_pct=threshold)

        engine = MarketReactionEngine()
        for interval, sources in ((5, 0), (5, 6), (0, 1)):
            with self.subTest(interval=interval, sources=sources):
                with self.assertRaises(ValueError):
                    engine.analyze(
                        _candle(interval_minutes=interval, source_minutes=sources),
                        reference_price=Decimal("100"),
                    )


if __name__ == "__main__":
    unittest.main()
