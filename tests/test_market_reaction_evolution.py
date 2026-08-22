import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.market_reaction import MarketReactionSnapshot, ReactionDirection
from trading_system.market_reaction_evolution import (
    MarketReactionEvolutionTracker,
    ReactionEvolution,
)


BASE_TIME = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _snapshot(
    return_pct: str,
    *,
    minutes: int = 0,
    interval: int = 5,
    tracked_id: str = "tracked-1",
    instrument: str = "NOKIA.HE",
    market: str = "Helsinki",
    etoro_id: int = 4242,
    reference: str = "100",
    source_minutes: int | None = None,
) -> MarketReactionSnapshot:
    pct = Decimal(return_pct)
    threshold = Decimal("0.25")
    if pct > threshold:
        direction = ReactionDirection.POSITIVE
    elif pct < -threshold:
        direction = ReactionDirection.NEGATIVE
    else:
        direction = ReactionDirection.FLAT
    reference_price = Decimal(reference)
    close_price = reference_price * (Decimal("1") + pct / Decimal("100"))
    return MarketReactionSnapshot(
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        interval_minutes=interval,
        candle_start=BASE_TIME + timedelta(minutes=minutes),
        reference_price=reference_price,
        close_price=close_price,
        return_pct=pct,
        direction=direction,
        source_minutes=source_minutes if source_minutes is not None else interval,
        complete_window=(source_minutes if source_minutes is not None else interval) == interval,
    )


class MarketReactionEvolutionTrackerTests(unittest.TestCase):
    def test_first_snapshot_is_initial_without_previous_delta(self) -> None:
        result = MarketReactionEvolutionTracker().add(_snapshot("-2.0"))

        self.assertEqual(result.evolution, ReactionEvolution.INITIAL)
        self.assertIsNone(result.previous_return_pct)
        self.assertIsNone(result.delta_pct_points)

    def test_same_direction_extends_retraces_and_stays_stable_by_magnitude(self) -> None:
        tracker = MarketReactionEvolutionTracker()
        tracker.add(_snapshot("-2.0", minutes=0))

        extending = tracker.add(_snapshot("-4.0", minutes=5))
        retracing = tracker.add(_snapshot("-3.0", minutes=10))
        stable = tracker.add(_snapshot("-3.0", minutes=15))

        self.assertEqual(extending.evolution, ReactionEvolution.EXTENDING)
        self.assertEqual(extending.previous_return_pct, Decimal("-2.0"))
        self.assertEqual(extending.delta_pct_points, Decimal("-2.0"))
        self.assertEqual(retracing.evolution, ReactionEvolution.RETRACING)
        self.assertEqual(stable.evolution, ReactionEvolution.STABLE)

    def test_non_flat_direction_flip_is_reversal(self) -> None:
        tracker = MarketReactionEvolutionTracker()
        tracker.add(_snapshot("-3.0", minutes=0))

        result = tracker.add(_snapshot("2.0", minutes=5))

        self.assertEqual(result.evolution, ReactionEvolution.REVERSAL)
        self.assertEqual(result.delta_pct_points, Decimal("5.0"))

    def test_flat_transition_neutralizes_then_non_flat_reactivates(self) -> None:
        tracker = MarketReactionEvolutionTracker()
        tracker.add(_snapshot("-2.0", minutes=0))

        neutralized = tracker.add(_snapshot("0.10", minutes=5))
        stable_flat = tracker.add(_snapshot("0.20", minutes=10))
        reactivated = tracker.add(_snapshot("1.5", minutes=15))

        self.assertEqual(neutralized.evolution, ReactionEvolution.NEUTRALIZED)
        self.assertEqual(stable_flat.evolution, ReactionEvolution.STABLE)
        self.assertEqual(reactivated.evolution, ReactionEvolution.REACTIVATED)

    def test_state_is_isolated_by_tracked_id_and_interval(self) -> None:
        tracker = MarketReactionEvolutionTracker()
        first_5m = tracker.add(_snapshot("1.0", interval=5, minutes=0))
        first_15m = tracker.add(_snapshot("1.0", interval=15, minutes=0))
        first_other = tracker.add(_snapshot("-1.0", tracked_id="tracked-2", etoro_id=5252, minutes=0))

        self.assertEqual(first_5m.evolution, ReactionEvolution.INITIAL)
        self.assertEqual(first_15m.evolution, ReactionEvolution.INITIAL)
        self.assertEqual(first_other.evolution, ReactionEvolution.INITIAL)

    def test_identity_or_reference_change_fails_closed_without_corrupting_state(self) -> None:
        tracker = MarketReactionEvolutionTracker()
        tracker.add(_snapshot("1.0", minutes=0))

        with self.assertRaisesRegex(ValueError, "identity or reference"):
            tracker.add(_snapshot("2.0", minutes=5, reference="101"))

        result = tracker.add(_snapshot("2.0", minutes=10))
        self.assertEqual(result.evolution, ReactionEvolution.EXTENDING)
        self.assertEqual(result.previous_return_pct, Decimal("1.0"))

    def test_non_chronological_snapshot_fails_closed_without_advancing_state(self) -> None:
        tracker = MarketReactionEvolutionTracker()
        tracker.add(_snapshot("1.0", minutes=5))

        with self.assertRaisesRegex(ValueError, "strictly chronological"):
            tracker.add(_snapshot("2.0", minutes=5))
        with self.assertRaisesRegex(ValueError, "strictly chronological"):
            tracker.add(_snapshot("2.0", minutes=0))

        result = tracker.add(_snapshot("2.0", minutes=10))
        self.assertEqual(result.previous_return_pct, Decimal("1.0"))
        self.assertEqual(result.evolution, ReactionEvolution.EXTENDING)

    def test_invalid_snapshot_metadata_fails_closed(self) -> None:
        tracker = MarketReactionEvolutionTracker()
        bad_return = _snapshot("1.0")
        object.__setattr__(bad_return, "return_pct", Decimal("NaN"))
        with self.assertRaisesRegex(ValueError, "return_pct"):
            tracker.add(bad_return)

        bad_source = _snapshot("1.0", source_minutes=6)
        with self.assertRaisesRegex(ValueError, "source_minutes"):
            tracker.add(bad_source)


if __name__ == "__main__":
    unittest.main()
