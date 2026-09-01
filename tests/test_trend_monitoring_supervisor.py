import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime
from trading_system.trend_monitoring_supervisor import TrendMonitoringSupervisor
from trading_system.trend_monitoring_targets import _selected_targets


def target(identifier: str, etoro_id: int, *, symbol: str | None = None) -> TrackedEtoroInstrument:
    ticker = symbol or identifier.upper()
    return TrackedEtoroInstrument(
        tracked_instrument_id=identifier,
        instrument=ticker,
        market="USA",
        etoro_instrument_id=etoro_id,
        etoro_symbol=ticker,
        etoro_display_name=f"{ticker} Inc",
        etoro_market="NASDAQ",
    )


def snapshot(*items: TrackedEtoroInstrument, unresolved=()):
    return _selected_targets(
        resolved=tuple(items),
        unresolved_tracked_instrument_ids=tuple(unresolved),
    )


def seed_runtime(runtime: TrendMonitoringRuntime, item: TrackedEtoroInstrument) -> None:
    candle = TrackedMarketCandle(
        tracked_instrument_id=item.tracked_instrument_id,
        instrument=item.instrument,
        market=item.market,
        etoro_instrument_id=item.etoro_instrument_id,
        interval_minutes=15,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        source_minutes=15,
    )
    runtime.add_candle(
        candle,
        instrument_active=True,
        trend_profile_enabled=True,
        etoro_identity_resolved=True,
    )


class TrendMonitoringSupervisorTests(unittest.TestCase):
    def test_first_refresh_requires_stream_start_and_same_snapshot_does_not_restart(self):
        runtime = TrendMonitoringRuntime()
        current = snapshot(target("a", 101), target("b", 202))
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: current, runtime=runtime)

        first = supervisor.refresh()
        second = supervisor.refresh()

        self.assertTrue(first.restart_required)
        self.assertFalse(second.restart_required)
        self.assertEqual(second.discarded_runtime_state_ids, ())
        self.assertIs(supervisor.targets, current)

    def test_order_only_change_does_not_restart(self):
        runtime = TrendMonitoringRuntime()
        a = target("a", 101)
        b = target("b", 202)
        snapshots = iter((snapshot(a, b), snapshot(b, a)))
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: next(snapshots), runtime=runtime)

        self.assertTrue(supervisor.refresh().restart_required)
        self.assertFalse(supervisor.refresh().restart_required)

    def test_removed_target_state_is_pruned_before_restart(self):
        runtime = TrendMonitoringRuntime()
        a = target("a", 101)
        b = target("b", 202)
        seed_runtime(runtime, a)
        seed_runtime(runtime, b)
        snapshots = iter((snapshot(a, b), snapshot(a)))
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: next(snapshots), runtime=runtime)

        supervisor.refresh()
        refreshed = supervisor.refresh()

        self.assertTrue(refreshed.restart_required)
        self.assertEqual(refreshed.discarded_runtime_state_ids, ("b",))
        self.assertIn("a", runtime._states)
        self.assertNotIn("b", runtime._states)

    def test_same_tracked_id_with_changed_broker_identity_discards_old_state(self):
        runtime = TrendMonitoringRuntime()
        original = target("a", 101)
        changed = replace(original, etoro_instrument_id=999, etoro_symbol="AAA.NEW")
        seed_runtime(runtime, original)
        snapshots = iter((snapshot(original), snapshot(changed)))
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: next(snapshots), runtime=runtime)

        supervisor.refresh()
        refreshed = supervisor.refresh()

        self.assertTrue(refreshed.restart_required)
        self.assertEqual(refreshed.discarded_runtime_state_ids, ("a",))
        self.assertNotIn("a", runtime._states)

    def test_unresolved_set_change_requires_restart_without_inventing_runtime_state(self):
        runtime = TrendMonitoringRuntime()
        a = target("a", 101)
        snapshots = iter((snapshot(a, unresolved=("b",)), snapshot(a)))
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: next(snapshots), runtime=runtime)

        supervisor.refresh()
        refreshed = supervisor.refresh()

        self.assertTrue(refreshed.restart_required)
        self.assertEqual(refreshed.discarded_runtime_state_ids, ())

    def test_noncanonical_selector_result_fails_closed(self):
        runtime = TrendMonitoringRuntime()
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: object(), runtime=runtime)
        with self.assertRaisesRegex(TypeError, "canonical TrendMonitoringTargets"):
            supervisor.refresh()


if __name__ == "__main__":
    unittest.main()
