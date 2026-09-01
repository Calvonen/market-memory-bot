import ast
import importlib.util
import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from trading_system.trend_monitoring_contract import (
    TREND_CANDLE_INTERVAL,
    TREND_CONFIRMATION_BARS,
    TREND_MIN_COMPLETED_BARS,
    TrendEvaluationInput,
    TrendState,
    apply_trend_confirmation,
    evaluate_trend,
)


def _imported_modules_from_source(module_source: str, package: str) -> set[str]:
    tree = ast.parse(module_source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative_name = "." * node.level + (node.module or "")
                imported_modules.add(importlib.util.resolve_name(relative_name, package))
            elif node.module:
                imported_modules.add(node.module)
    return imported_modules


class TrendMonitoringContractTests(unittest.TestCase):
    def ready_snapshot(self, **overrides):
        values = {
            "close": 120.0,
            "ema_fast": 110.0,
            "ema_slow": 100.0,
            "ema_fast_lookback": 108.0,
            "completed_bars": TREND_MIN_COMPLETED_BARS,
            "candle_closed_at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            "candle_closed": True,
            "instrument_active": True,
            "trend_profile_enabled": True,
            "etoro_identity_resolved": True,
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
            self.ready_snapshot(candle_closed_at=datetime(2026, 9, 1, 12, 0)),
        ):
            observation = evaluate_trend(snapshot)
            self.assertFalse(observation.ready)
            self.assertEqual(observation.candidate_state, TrendState.UNKNOWN)
            self.assertEqual(observation.reason, "trend_prerequisites_not_ready")

    def test_prerequisite_evidence_is_explicit(self):
        with self.assertRaises(TypeError):
            TrendEvaluationInput(
                close=120.0,
                ema_fast=110.0,
                ema_slow=100.0,
                ema_fast_lookback=108.0,
                completed_bars=TREND_MIN_COMPLETED_BARS,
                candle_closed_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            )

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

    def test_state_changes_only_after_three_distinct_consecutive_completed_candles(self):
        state = TrendState.NEUTRAL
        pending_candidate = None
        pending_count = 0
        pending_last_candle_at = None
        last_processed_candle_at = None
        base = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

        for index in range(TREND_CONFIRMATION_BARS):
            bullish = evaluate_trend(
                self.ready_snapshot(candle_closed_at=base + index * TREND_CANDLE_INTERVAL)
            )
            transition = apply_trend_confirmation(
                current_state=state,
                observation=bullish,
                pending_candidate=pending_candidate,
                pending_count=pending_count,
                pending_last_candle_at=pending_last_candle_at,
                last_processed_candle_at=last_processed_candle_at,
            )
            if index < TREND_CONFIRMATION_BARS - 1:
                self.assertFalse(transition.changed)
                self.assertEqual(transition.pending_count, index + 1)
            else:
                self.assertTrue(transition.changed)
                self.assertEqual(transition.state, TrendState.BULLISH)
            pending_candidate = transition.pending_candidate
            pending_count = transition.pending_count
            pending_last_candle_at = transition.pending_last_candle_at
            last_processed_candle_at = transition.last_processed_candle_at

    def test_duplicate_completed_candle_does_not_advance_confirmation(self):
        candle_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        bullish = evaluate_trend(self.ready_snapshot(candle_closed_at=candle_at))
        first = apply_trend_confirmation(
            current_state=TrendState.NEUTRAL,
            observation=bullish,
        )
        duplicate = apply_trend_confirmation(
            current_state=first.state,
            observation=bullish,
            pending_candidate=first.pending_candidate,
            pending_count=first.pending_count,
            pending_last_candle_at=first.pending_last_candle_at,
            last_processed_candle_at=first.last_processed_candle_at,
        )
        self.assertFalse(duplicate.changed)
        self.assertEqual(duplicate.pending_count, 1)
        self.assertEqual(duplicate.last_processed_candle_at, candle_at)

    def test_gap_breaks_consecutive_confirmation_chain(self):
        first_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        first = apply_trend_confirmation(
            current_state=TrendState.NEUTRAL,
            observation=evaluate_trend(self.ready_snapshot(candle_closed_at=first_at)),
        )
        gap_observation = evaluate_trend(
            self.ready_snapshot(candle_closed_at=first_at + 2 * TREND_CANDLE_INTERVAL)
        )
        after_gap = apply_trend_confirmation(
            current_state=first.state,
            observation=gap_observation,
            pending_candidate=first.pending_candidate,
            pending_count=first.pending_count,
            pending_last_candle_at=first.pending_last_candle_at,
            last_processed_candle_at=first.last_processed_candle_at,
        )
        self.assertEqual(after_gap.pending_count, 1)
        self.assertFalse(after_gap.changed)

    def test_dst_wall_clock_sequence_is_checked_in_utc(self):
        helsinki = ZoneInfo("Europe/Helsinki")
        first_at = datetime(2026, 10, 25, 2, 45, tzinfo=helsinki)
        second_at = datetime(2026, 10, 25, 3, 0, tzinfo=helsinki, fold=0)
        third_at = datetime(2026, 10, 25, 3, 15, tzinfo=helsinki, fold=1)

        first = apply_trend_confirmation(
            current_state=TrendState.NEUTRAL,
            observation=evaluate_trend(self.ready_snapshot(candle_closed_at=first_at)),
        )
        second = apply_trend_confirmation(
            current_state=first.state,
            observation=evaluate_trend(self.ready_snapshot(candle_closed_at=second_at)),
            pending_candidate=first.pending_candidate,
            pending_count=first.pending_count,
            pending_last_candle_at=first.pending_last_candle_at,
            last_processed_candle_at=first.last_processed_candle_at,
        )
        third = apply_trend_confirmation(
            current_state=second.state,
            observation=evaluate_trend(self.ready_snapshot(candle_closed_at=third_at)),
            pending_candidate=second.pending_candidate,
            pending_count=second.pending_count,
            pending_last_candle_at=second.pending_last_candle_at,
            last_processed_candle_at=second.last_processed_candle_at,
        )

        self.assertFalse(third.changed)
        self.assertEqual(third.state, TrendState.NEUTRAL)
        self.assertEqual(third.pending_count, 1)
        self.assertEqual(third.pending_last_candle_at.tzinfo, UTC)

    def test_unknown_breaks_pending_confirmation_without_erasing_known_state(self):
        candle_at = datetime(2026, 9, 1, 12, 30, tzinfo=UTC)
        unknown = evaluate_trend(self.ready_snapshot(candle_closed=False, candle_closed_at=candle_at))
        transition = apply_trend_confirmation(
            current_state=TrendState.BULLISH,
            observation=unknown,
            pending_candidate=TrendState.BEARISH,
            pending_count=2,
            pending_last_candle_at=candle_at - TREND_CANDLE_INTERVAL,
            last_processed_candle_at=candle_at - TREND_CANDLE_INTERVAL,
        )
        self.assertFalse(transition.changed)
        self.assertEqual(transition.state, TrendState.BULLISH)
        self.assertIsNone(transition.pending_candidate)
        self.assertEqual(transition.pending_count, 0)

    def test_relative_execution_imports_resolve_to_canonical_package(self):
        imported_modules = _imported_modules_from_source(
            "from .brokers.paper import PaperBroker\n",
            "trading_system",
        )
        self.assertIn("trading_system.brokers.paper", imported_modules)

    def test_contract_has_no_execution_dependencies(self):
        module = __import__("trading_system.trend_monitoring_contract", fromlist=["dummy"])
        module_source = open(module.__file__, encoding="utf-8").read()
        imported_modules = _imported_modules_from_source(module_source, module.__package__)
        forbidden_prefixes = (
            "requests",
            "websockets",
            "trading_system.strategy",
            "trading_system.risk",
            "trading_system.paper",
            "trading_system.broker",
            "trading_system.brokers",
            "trading_system.tracked_event",
        )
        self.assertFalse(
            any(
                imported == prefix or imported.startswith(prefix + ".")
                for imported in imported_modules
                for prefix in forbidden_prefixes
            )
        )


if __name__ == "__main__":
    unittest.main()
