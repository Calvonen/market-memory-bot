from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from tests.test_post_release_confirmation_fallback import (
    ANCHOR,
    _event,
    _flat_anchor,
    _observation_close,
    _reaction,
    _snapshot,
)
from trading_system.earnings_confirmation_horizon import (
    evaluate_earnings_confirmation_horizon,
)


class EarningsConfirmationHorizonTests(unittest.TestCase):
    def test_last_persistable_candle_before_aligned_horizon_proves_complete(self) -> None:
        closing = _reaction(
            interval_minutes=15,
            candle_start=ANCHOR + timedelta(hours=7, minutes=30),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        decision = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=_snapshot()),
            reactions=(_flat_anchor(), _observation_close(), closing),
        )

        self.assertTrue(decision.complete)
        self.assertEqual(decision.reason, "terminal_persistable_reaction")

    def test_multi_minute_boundary_reserves_one_minute_emission_lead(self) -> None:
        snapshot = _snapshot(monitor_hours=8.0001)
        too_late = _reaction(
            interval_minutes=15,
            candle_start=ANCHOR + timedelta(hours=7, minutes=45),
            close_price=Decimal("100.10"),
            direction="flat",
        )
        persistable = _reaction(
            interval_minutes=15,
            candle_start=ANCHOR + timedelta(hours=7, minutes=30),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        incomplete = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=snapshot),
            reactions=(_flat_anchor(), _observation_close(), too_late),
        )
        complete = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=snapshot),
            reactions=(_flat_anchor(), _observation_close(), persistable),
        )

        self.assertFalse(incomplete.complete)
        self.assertTrue(complete.complete)

    def test_stage_boundary_just_after_30_minutes_uses_terminal_1m_candle(self) -> None:
        snapshot = _snapshot(monitor_hours=30.1 / 60)
        terminal = _reaction(
            interval_minutes=1,
            candle_start=ANCHOR + timedelta(minutes=29),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        decision = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=snapshot),
            reactions=(_flat_anchor(), terminal),
        )

        self.assertTrue(decision.complete)

    def test_31_minute_cadence_gap_retains_terminal_1m_candle(self) -> None:
        snapshot = _snapshot(monitor_hours=31 / 60)
        terminal = _reaction(
            interval_minutes=1,
            candle_start=ANCHOR + timedelta(minutes=29),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        decision = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=snapshot),
            reactions=(_flat_anchor(), terminal),
        )

        self.assertTrue(decision.complete)

    def test_stage_boundary_just_after_150_minutes_uses_last_emittable_5m_candle(self) -> None:
        snapshot = _snapshot(monitor_hours=150.1 / 60)
        too_late = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=145),
            close_price=Decimal("100.10"),
            direction="flat",
        )
        persistable = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=140),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        incomplete = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=snapshot),
            reactions=(_flat_anchor(), _observation_close(), too_late),
        )
        complete = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=snapshot),
            reactions=(_flat_anchor(), _observation_close(), persistable),
        )

        self.assertFalse(incomplete.complete)
        self.assertTrue(complete.complete)

    def test_156_minute_cadence_gap_retains_terminal_5m_candle(self) -> None:
        snapshot = _snapshot(monitor_hours=156 / 60)
        terminal = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=145),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        decision = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=snapshot),
            reactions=(_flat_anchor(), _observation_close(), terminal),
        )

        self.assertTrue(decision.complete)

    def test_initial_window_terminal_evidence_can_prove_complete(self) -> None:
        snapshot = _snapshot(monitor_hours=0.5)
        terminal = _reaction(
            interval_minutes=1,
            candle_start=ANCHOR + timedelta(minutes=29),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        decision = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=snapshot),
            reactions=(_flat_anchor(), terminal),
        )

        self.assertTrue(decision.complete)
        self.assertEqual(decision.reason, "terminal_persistable_reaction")

    def test_misaligned_horizon_uses_last_persistable_canonical_boundary(self) -> None:
        snapshot = _snapshot(monitor_hours=8.1)
        closing = _reaction(
            interval_minutes=15,
            candle_start=ANCHOR + timedelta(hours=7, minutes=45),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        decision = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=snapshot),
            reactions=(_flat_anchor(), _observation_close(), closing),
        )

        self.assertTrue(decision.complete)
        self.assertEqual(decision.reason, "terminal_persistable_reaction")

    def test_earlier_evidence_never_proves_complete(self) -> None:
        earlier = _reaction(
            interval_minutes=15,
            candle_start=ANCHOR + timedelta(hours=7, minutes=15),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        decision = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=_snapshot()),
            reactions=(_flat_anchor(), _observation_close(), earlier),
        )

        self.assertFalse(decision.complete)
        self.assertEqual(decision.reason, "horizon_evidence_incomplete")

    def test_legacy_event_without_snapshot_can_never_expire_from_new_rule(self) -> None:
        decision = evaluate_earnings_confirmation_horizon(
            event=_event(tracking_config_snapshot=None),
            reactions=(_flat_anchor(), _observation_close()),
        )

        self.assertFalse(decision.complete)
        self.assertEqual(decision.reason, "tracking_snapshot_missing")

    def test_noncanonical_profile_fails_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["reaction_stages"] = [
            {"start_after_minutes": 0, "interval_minutes": 1},
            {"start_after_minutes": 45, "interval_minutes": 5},
        ]

        with self.assertRaisesRegex(ValueError, "differs from canonical reaction profile"):
            evaluate_earnings_confirmation_horizon(
                event=_event(tracking_config_snapshot=snapshot),
                reactions=(_flat_anchor(), _observation_close()),
            )


if __name__ == "__main__":
    unittest.main()
