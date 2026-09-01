import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.trend_monitoring_contract import TrendState
from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime


class TrendMonitoringRuntimeTests(unittest.TestCase):
    def candle(
        self,
        index: int,
        *,
        close: float | None = None,
        interval_minutes: int = 15,
        source_minutes: int = 15,
        instrument: str = "TEST",
        etoro_instrument_id: int = 123,
    ) -> TrackedMarketCandle:
        price = Decimal(str(close if close is not None else index + 1))
        start = datetime(2026, 1, 1, tzinfo=UTC) + index * timedelta(minutes=15)
        return TrackedMarketCandle(
            tracked_instrument_id="tracked-1",
            instrument=instrument,
            market="USA",
            etoro_instrument_id=etoro_instrument_id,
            interval_minutes=interval_minutes,
            start=start,
            open=price,
            high=price,
            low=price,
            close=price,
            source_minutes=source_minutes,
        )

    def add_ready(self, runtime: TrendMonitoringRuntime, candle: TrackedMarketCandle):
        return runtime.add_candle(
            candle,
            instrument_active=True,
            trend_profile_enabled=True,
            etoro_identity_resolved=True,
        )

    def test_non_15_minute_candles_are_ignored(self):
        runtime = TrendMonitoringRuntime()
        self.assertIsNone(self.add_ready(runtime, self.candle(0, interval_minutes=5, source_minutes=5)))

    def test_runtime_fails_closed_until_indicator_warmup_is_complete(self):
        runtime = TrendMonitoringRuntime()
        result = None
        for index in range(203):
            result = self.add_ready(runtime, self.candle(index))
        self.assertIsNotNone(result)
        self.assertFalse(result.observation.ready)
        self.assertEqual(result.observation.candidate_state, TrendState.UNKNOWN)
        self.assertEqual(result.transition.state, TrendState.UNKNOWN)

    def test_three_ready_rising_candles_confirm_bullish_after_warmup(self):
        runtime = TrendMonitoringRuntime()
        results = []
        for index in range(206):
            result = self.add_ready(runtime, self.candle(index))
            if index >= 203:
                results.append(result)

        self.assertEqual(len(results), 3)
        self.assertTrue(all(item is not None for item in results))
        self.assertEqual(results[0].observation.candidate_state, TrendState.BULLISH)
        self.assertEqual(results[0].transition.pending_count, 1)
        self.assertEqual(results[1].transition.pending_count, 2)
        self.assertTrue(results[2].transition.changed)
        self.assertEqual(results[2].transition.state, TrendState.BULLISH)

    def test_sparse_15_minute_candle_is_unknown_and_breaks_pending_confirmation(self):
        runtime = TrendMonitoringRuntime()
        for index in range(204):
            result = self.add_ready(runtime, self.candle(index))
        self.assertEqual(result.transition.pending_count, 1)

        sparse = self.add_ready(runtime, self.candle(204, source_minutes=14))
        self.assertIsNotNone(sparse)
        self.assertFalse(sparse.observation.ready)
        self.assertEqual(sparse.observation.candidate_state, TrendState.UNKNOWN)
        self.assertEqual(sparse.transition.pending_count, 0)
        self.assertEqual(sparse.transition.state, TrendState.UNKNOWN)

    def test_duplicate_candle_is_idempotent_and_conflicting_duplicate_fails(self):
        runtime = TrendMonitoringRuntime()
        original = self.candle(0, close=100)
        self.assertIsNotNone(self.add_ready(runtime, original))
        self.assertIsNone(self.add_ready(runtime, original))
        with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
            self.add_ready(runtime, self.candle(0, close=101))

    def test_out_of_order_and_identity_mutation_fail_closed(self):
        runtime = TrendMonitoringRuntime()
        self.add_ready(runtime, self.candle(1))
        with self.assertRaisesRegex(ValueError, "out of order"):
            self.add_ready(runtime, self.candle(0))

        runtime = TrendMonitoringRuntime()
        self.add_ready(runtime, self.candle(0))
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.add_ready(runtime, self.candle(1, instrument="OTHER"))

    def test_naive_timestamp_is_rejected_before_indicator_state_mutates(self):
        runtime = TrendMonitoringRuntime()
        valid = self.candle(0, close=100)
        naive = replace(valid, start=valid.start.replace(tzinfo=None))

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.add_ready(runtime, naive)

        self.assertNotIn("tracked-1", runtime._states)
        self.assertIsNotNone(self.add_ready(runtime, valid))
        self.assertEqual(runtime._states["tracked-1"].completed_bars, 1)

    def test_non_finite_float_close_is_rejected_before_state_mutates(self):
        runtime = TrendMonitoringRuntime()
        valid = self.candle(0, close=100)
        huge = Decimal("1e10000")
        invalid = replace(valid, open=huge, high=huge, low=huge, close=huge)

        with self.assertRaisesRegex(ValueError, "finite and positive"):
            self.add_ready(runtime, invalid)

        self.assertNotIn("tracked-1", runtime._states)
        self.assertIsNotNone(self.add_ready(runtime, valid))
        self.assertEqual(runtime._states["tracked-1"].completed_bars, 1)

    def test_discard_forgets_gap_sensitive_state_before_reenable(self):
        runtime = TrendMonitoringRuntime()
        self.add_ready(runtime, self.candle(0, close=100))
        self.assertTrue(runtime.discard_tracked_instrument(" tracked-1 "))
        self.assertFalse(runtime.discard_tracked_instrument("tracked-1"))

        replacement = self.candle(0, close=200, instrument="NEW", etoro_instrument_id=456)
        self.assertIsNotNone(self.add_ready(runtime, replacement))
        self.assertEqual(runtime._states["tracked-1"].completed_bars, 1)
        self.assertEqual(runtime._states["tracked-1"].instrument, "NEW")

    def test_retain_preserves_current_targets_and_discards_removed_targets(self):
        runtime = TrendMonitoringRuntime()
        first = self.candle(0, close=100)
        second = replace(
            self.candle(0, close=200, instrument="SECOND", etoro_instrument_id=456),
            tracked_instrument_id="tracked-2",
        )
        self.add_ready(runtime, first)
        self.add_ready(runtime, second)

        discarded = runtime.retain_tracked_instruments({" tracked-1 "})

        self.assertEqual(discarded, ("tracked-2",))
        self.assertIn("tracked-1", runtime._states)
        self.assertNotIn("tracked-2", runtime._states)
        self.assertEqual(runtime._states["tracked-1"].completed_bars, 1)

    def test_state_pruning_rejects_blank_identity(self):
        runtime = TrendMonitoringRuntime()
        with self.assertRaisesRegex(ValueError, "tracked_instrument_id"):
            runtime.discard_tracked_instrument("   ")
        with self.assertRaisesRegex(ValueError, "tracked_instrument_id"):
            runtime.retain_tracked_instruments({"tracked-1", "   "})

    def test_disabled_profile_cannot_emit_directional_ready_state(self):
        runtime = TrendMonitoringRuntime()
        for index in range(204):
            result = runtime.add_candle(
                self.candle(index),
                instrument_active=True,
                trend_profile_enabled=False,
                etoro_identity_resolved=True,
            )
        self.assertFalse(result.observation.ready)
        self.assertEqual(result.observation.candidate_state, TrendState.UNKNOWN)
        self.assertEqual(result.transition.state, TrendState.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
