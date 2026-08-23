from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.event_reaction_orchestration import EventReactionOrchestrator
from trading_system.market_reaction import ReactionDirection
from trading_system.market_reaction_evolution import ReactionEvolution
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    etoro_instrument_id=123,
    etoro_symbol="ABC.L",
    etoro_display_name="ABC plc",
)
EVENT_AT = datetime(2026, 8, 23, 7, 5, tzinfo=UTC)


def _candle(
    minute: int,
    close: str,
    *,
    interval: int = 1,
    source_minutes: int | None = None,
    tracked_id: str = "tracked-1",
    instrument: str = "ABC",
    market: str = "LSE",
    etoro_id: int = 123,
) -> TrackedMarketCandle:
    price = Decimal(close)
    return TrackedMarketCandle(
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        interval_minutes=interval,
        start=datetime(2026, 8, 23, 7, minute, tzinfo=UTC),
        open=price,
        high=price,
        low=price,
        close=price,
        source_minutes=interval if source_minutes is None else source_minutes,
    )


class EventReactionOrchestratorTests(unittest.TestCase):
    def test_selects_latest_matching_reference_and_analyzes_post_event_candle(self) -> None:
        orchestrator = EventReactionOrchestrator()

        result = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(2, "100"), _candle(4, "99")),
            reaction_candle=_candle(5, "95"),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reference.source_candle_start, _candle(4, "99").start)
        self.assertEqual(result.reference.baseline.reference_price, Decimal("99"))
        self.assertEqual(
            result.reaction.tracked_reaction.reaction.direction,
            ReactionDirection.NEGATIVE,
        )
        self.assertEqual(
            result.reaction.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )

    def test_same_event_continues_reaction_evolution_with_same_reference(self) -> None:
        orchestrator = EventReactionOrchestrator()
        references = (_candle(4, "100"),)

        first = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=references,
            reaction_candle=_candle(5, "98"),
        )
        second = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=references,
            reaction_candle=_candle(6, "96"),
        )

        assert first is not None and second is not None
        self.assertEqual(
            first.reaction.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )
        self.assertEqual(
            second.reaction.tracked_reaction.evolution.evolution,
            ReactionEvolution.EXTENDING,
        )

    def test_fixed_reference_interval_survives_reaction_interval_switch(self) -> None:
        orchestrator = EventReactionOrchestrator()
        references = (
            _candle(0, "90", interval=5),
            _candle(4, "100", interval=1),
        )

        one_minute = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=references,
            reaction_candle=_candle(5, "98", interval=1),
            reference_interval_minutes=1,
        )
        five_minute = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=references,
            reaction_candle=_candle(10, "95", interval=5),
            reference_interval_minutes=1,
        )

        assert one_minute is not None and five_minute is not None
        self.assertEqual(one_minute.reference.interval_minutes, 1)
        self.assertEqual(five_minute.reference.interval_minutes, 1)
        self.assertEqual(one_minute.reference.source_candle_start, _candle(4, "100").start)
        self.assertEqual(five_minute.reference.source_candle_start, _candle(4, "100").start)
        self.assertEqual(one_minute.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(five_minute.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(five_minute.reaction.tracked_reaction.reaction.interval_minutes, 5)

    def test_no_reference_returns_none_without_starting_reaction_sequence(self) -> None:
        orchestrator = EventReactionOrchestrator()

        missing = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(5, "100"),),
            reaction_candle=_candle(6, "98"),
        )
        valid = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candle=_candle(6, "98"),
        )

        self.assertIsNone(missing)
        assert valid is not None
        self.assertEqual(
            valid.reaction.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )

    def test_reaction_candle_identity_mismatch_fails_before_state_is_touched(self) -> None:
        orchestrator = EventReactionOrchestrator()

        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            orchestrator.add(
                event_id="event-1",
                event_at=EVENT_AT,
                tracked=TRACKED,
                reference_candles=(_candle(4, "100"),),
                reaction_candle=_candle(5, "98", etoro_id=999),
            )

        valid = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candle=_candle(5, "98"),
        )
        assert valid is not None
        self.assertEqual(
            valid.reaction.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )

    def test_pre_event_or_overlapping_reaction_candle_is_rejected(self) -> None:
        orchestrator = EventReactionOrchestrator()

        with self.assertRaisesRegex(ValueError, "start at or after event_at"):
            orchestrator.add(
                event_id="event-1",
                event_at=datetime(2026, 8, 23, 7, 5, 30, tzinfo=UTC),
                tracked=TRACKED,
                reference_candles=(_candle(4, "100"),),
                reaction_candle=_candle(5, "98"),
            )

    def test_incomplete_reaction_window_is_rejected(self) -> None:
        orchestrator = EventReactionOrchestrator()

        with self.assertRaisesRegex(ValueError, "complete window"):
            orchestrator.add(
                event_id="event-1",
                event_at=EVENT_AT,
                tracked=TRACKED,
                reference_candles=(_candle(0, "100", interval=5),),
                reaction_candle=_candle(5, "98", interval=5, source_minutes=4),
            )

    def test_changed_reference_for_started_event_fails_closed(self) -> None:
        orchestrator = EventReactionOrchestrator()

        first = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(3, "100"),),
            reaction_candle=_candle(5, "98"),
        )
        self.assertIsNotNone(first)

        with self.assertRaisesRegex(ValueError, "event baseline changed"):
            orchestrator.add(
                event_id="event-1",
                event_at=EVENT_AT,
                tracked=TRACKED,
                reference_candles=(_candle(4, "99"),),
                reaction_candle=_candle(6, "97"),
            )

    def test_same_event_different_source_candle_same_price_fails_closed(self) -> None:
        orchestrator = EventReactionOrchestrator()

        first = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(3, "100"),),
            reaction_candle=_candle(5, "98"),
        )
        self.assertIsNotNone(first)

        with self.assertRaisesRegex(ValueError, "event reference changed"):
            orchestrator.add(
                event_id="event-1",
                event_at=EVENT_AT,
                tracked=TRACKED,
                reference_candles=(_candle(4, "100"),),
                reaction_candle=_candle(6, "97"),
            )

    def test_reference_mismatch_failure_preserves_original_reference_and_state(self) -> None:
        orchestrator = EventReactionOrchestrator()

        first = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(3, "100"),),
            reaction_candle=_candle(5, "98"),
        )
        self.assertIsNotNone(first)

        with self.assertRaisesRegex(ValueError, "event reference changed"):
            orchestrator.add(
                event_id="event-1",
                event_at=EVENT_AT,
                tracked=TRACKED,
                reference_candles=(_candle(4, "100"),),
                reaction_candle=_candle(6, "97"),
            )

        second = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(3, "100"),),
            reaction_candle=_candle(6, "97"),
        )
        assert second is not None
        self.assertEqual(second.reference.source_candle_start, _candle(3, "100").start)
        self.assertEqual(
            second.reaction.tracked_reaction.evolution.evolution,
            ReactionEvolution.EXTENDING,
        )

    def test_evicted_pre_event_candle_still_uses_locked_reference(self) -> None:
        orchestrator = EventReactionOrchestrator()

        first = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candle=_candle(5, "98"),
        )
        self.assertIsNotNone(first)
        assert first is not None

        second = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(),
            reaction_candle=_candle(6, "96"),
        )

        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.reference, first.reference)
        self.assertEqual(second.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(
            second.reaction.tracked_reaction.evolution.evolution,
            ReactionEvolution.EXTENDING,
        )

    def test_reaction_continues_at_fifteen_minute_stage_after_reference_eviction(self) -> None:
        orchestrator = EventReactionOrchestrator()

        first = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candle=_candle(5, "98"),
            reference_interval_minutes=1,
        )
        self.assertIsNotNone(first)

        second = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(),
            reaction_candle=TrackedMarketCandle(
                tracked_instrument_id="tracked-1",
                instrument="ABC",
                market="LSE",
                etoro_instrument_id=123,
                interval_minutes=15,
                start=datetime(2026, 8, 23, 9, 45, tzinfo=UTC),
                open=Decimal("85"),
                high=Decimal("85"),
                low=Decimal("85"),
                close=Decimal("85"),
                source_minutes=15,
            ),
            reference_interval_minutes=1,
        )

        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(second.reaction.tracked_reaction.reaction.interval_minutes, 15)

    def test_new_conflicting_reference_still_fails_closed(self) -> None:
        orchestrator = EventReactionOrchestrator()

        first = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(_candle(3, "100"),),
            reaction_candle=_candle(5, "98"),
        )
        self.assertIsNotNone(first)

        with self.assertRaisesRegex(ValueError, "event reference changed"):
            orchestrator.add(
                event_id="event-1",
                event_at=EVENT_AT,
                tracked=TRACKED,
                reference_candles=(_candle(4, "100"),),
                reaction_candle=_candle(6, "97"),
            )

    def test_event_that_never_had_a_reference_still_returns_none(self) -> None:
        orchestrator = EventReactionOrchestrator()

        result = orchestrator.add(
            event_id="event-1",
            event_at=EVENT_AT,
            tracked=TRACKED,
            reference_candles=(),
            reaction_candle=_candle(6, "97"),
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
