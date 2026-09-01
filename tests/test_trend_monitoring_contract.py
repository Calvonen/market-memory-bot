import unittest

from trading_system.trend_monitoring_contract import (
    TREND_CONFIRMATION_BARS,
    TREND_MIN_COMPLETED_BARS,
    TrendEvaluationInput,
    TrendState,
    apply_trend_confirmation,
    evaluate_trend,
)


class TrendMonitoringContractTests(unittest.TestCase):
    def ready_snapshot(self, **overrides):
        values = {
            "close": 120.0,
            "ema_fast": 110.0,
            "ema_slow": 100.0,
            "ema_fast_lookback": 108.0,
            "completed_bars": TREND_MIN_COMPLETED_BARS,
        }
        values.update(overrides)
        return TrendEvaluationInput(**values)

    def test_contract_is_fail_closed_until_prerequisites_are_ready(self):
        for snapshot in (
            self.ready_snapshot(instrument_active=False),
            self.ready_snapshot(trend_profile_enabled=False),
            self.ready_snapshot(etoro_identity_resolved=False),
            self.ready_snapshot(candle_closed=False),
            self.ready_snapshot(completed_bars=TREND_MIN_COMPLETED_BARS - 1),
            self.ready_snapshot(close=0.0),
        ):
            observation = evaluate_trend(snapshot)
            self.assertFalse(observation.ready)
            self.assertEqual(observation.candidate_state, TrendState.UNKNOWN)
            self.assertEqual(observation.reason, "trend_prerequisites_not_ready")

    def test_bullish_requires_price_above_rising_ema50_above_ema200(self):
        observation = evaluate_trend(self.ready_snapshot())
        self.assertTrue(observation.ready)
        self.assertEqual(observation.candidate_state, TrendState.BULLISH)

    def test_bearish_is_exact_inverse(self):
        observation = evaluate_trend(
            self.ready_snapshot(
                close=80.0,
                ema_fast=90.0,
                ema_slow=100.0,
                ema_fast_lookback=92.0,
            )
        )
        self.assertEqual(observation.candidate_state, TrendState.BEARISH)

    def test_non_aligned_ready_snapshot_is_neutral(self):
        observation = evaluate_trend(
            self.ready_snapshot(close=105.0, ema_fast=110.0, ema_slow=100.0)
        )
        self.assertTrue(observation.ready)
        self.assertEqual(observation.candidate_state, TrendState.NEUTRAL)

    def test_state_changes_only_after_three_consecutive_completed_candle_candidates(self):
        bullish = evaluate_trend(self.ready_snapshot())
        state = TrendState.NEUTRAL
        pending_candidate = None
        pending_count = 0

        for expected_count in range(1, TREND_CONFIRMATION_BARS):
            transition = apply_trend_confirmation(
                current_state=state,
                observation=bullish,
                pending_candidate=pending_candidate,
                pending_count=pending_count,
            )
            self.assertFalse(transition.changed)
            self.assertEqual(transition.state, TrendState.NEUTRAL)
            self.assertEqual(transition.pending_candidate, TrendState.BULLISH)
            self.assertEqual(transition.pending_count, expected_count)
            pending_candidate = transition.pending_candidate
            pending_count = transition.pending_count

        transition = apply_trend_confirmation(
            current_state=state,
            observation=bullish,
            pending_candidate=pending_candidate,
            pending_count=pending_count,
        )
        self.assertTrue(transition.changed)
        self.assertEqual(transition.state, TrendState.BULLISH)
        self.assertIsNone(transition.pending_candidate)
        self.assertEqual(transition.pending_count, 0)

    def test_unknown_breaks_pending_confirmation_without_erasing_known_state(self):
        unknown = evaluate_trend(self.ready_snapshot(candle_closed=False))
        transition = apply_trend_confirmation(
            current_state=TrendState.BULLISH,
            observation=unknown,
            pending_candidate=TrendState.BEARISH,
            pending_count=2,
        )
        self.assertFalse(transition.changed)
        self.assertEqual(transition.state, TrendState.BULLISH)
        self.assertIsNone(transition.pending_candidate)
        self.assertEqual(transition.pending_count, 0)

    def test_contract_has_no_execution_side_effects(self):
        source = __import__(
            "trading_system.trend_monitoring_contract",
            fromlist=["dummy"],
        )
        module_source = open(source.__file__, encoding="utf-8").read()
        for forbidden in (
            "PaperBroker",
            "LiveBroker",
            "RiskEngine",
            "Strategy",
            "tracked_event",
            "requests.",
            "websockets",
        ):
            self.assertNotIn(forbidden, module_source)


if __name__ == "__main__":
    unittest.main()
