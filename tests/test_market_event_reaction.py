from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.market_event_reaction import MarketEventReactionBridge
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
EVENT = MarketEvent(
    event_id="event-1",
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    event_at=datetime(2026, 8, 23, 7, 5, tzinfo=UTC),
    source=MarketEventSource.RELEASE,
    kind=MarketEventKind.TRADING_UPDATE,
    title="Trading update",
)


def _candle(
    minute: int,
    close: str,
    *,
    tracked_id: str = "tracked-1",
    interval: int = 1,
    hour: int = 7,
) -> TrackedMarketCandle:
    price = Decimal(close)
    return TrackedMarketCandle(
        tracked_instrument_id=tracked_id,
        instrument="ABC",
        market="LSE",
        etoro_instrument_id=123,
        interval_minutes=interval,
        start=datetime(2026, 8, 23, hour, minute, tzinfo=UTC),
        open=price,
        high=price,
        low=price,
        close=price,
        source_minutes=interval,
    )


class MarketEventReactionBridgeTests(unittest.TestCase):
    def test_generic_event_drives_existing_reaction_pipeline(self) -> None:
        bridge = MarketEventReactionBridge()

        result = bridge.add(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candle=_candle(5, "96"),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reference.baseline.event_id, EVENT.event_id)
        self.assertEqual(result.reference.event_at, EVENT.event_at)
        self.assertEqual(result.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(
            result.reaction.tracked_reaction.reaction.direction,
            ReactionDirection.NEGATIVE,
        )
        self.assertEqual(
            result.reaction.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )

    def test_non_earnings_event_kind_is_supported_without_special_case(self) -> None:
        event = MarketEvent(
            event_id="event-guidance",
            tracked_instrument_id="tracked-1",
            instrument="ABC",
            market="LSE",
            event_at=EVENT.event_at,
            source=MarketEventSource.MANUAL,
            kind=MarketEventKind.GUIDANCE,
            title="Guidance change",
        )
        bridge = MarketEventReactionBridge()

        result = bridge.add(
            event=event,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candle=_candle(5, "103"),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reference.baseline.event_id, "event-guidance")
        self.assertEqual(
            result.reaction.tracked_reaction.reaction.direction,
            ReactionDirection.POSITIVE,
        )

    def test_event_identity_mismatch_fails_before_orchestrator_state_is_touched(self) -> None:
        bridge = MarketEventReactionBridge()
        mismatched = MarketEvent(
            event_id="event-1",
            tracked_instrument_id="other-tracked",
            instrument="ABC",
            market="LSE",
            event_at=EVENT.event_at,
            source=MarketEventSource.NEWS,
            kind=MarketEventKind.NEWS,
        )

        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            bridge.add(
                event=mismatched,
                tracked=TRACKED,
                reference_candles=(_candle(4, "100"),),
                reaction_candle=_candle(5, "96"),
            )

        valid = bridge.add(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candle=_candle(5, "96"),
        )
        assert valid is not None
        self.assertEqual(
            valid.reaction.tracked_reaction.evolution.evolution,
            ReactionEvolution.INITIAL,
        )

    def test_market_case_difference_is_not_treated_as_identity_mismatch(self) -> None:
        bridge = MarketEventReactionBridge()
        tracked_mixed_case = TrackedEtoroInstrument(
            tracked_instrument_id="tracked-1",
            instrument="ABC",
            market="Lse",
            etoro_instrument_id=123,
            etoro_symbol="ABC.L",
            etoro_display_name="ABC plc",
        )

        def candle_for_tracked(minute: int, close: str) -> TrackedMarketCandle:
            price = Decimal(close)
            return TrackedMarketCandle(
                tracked_instrument_id="tracked-1",
                instrument="ABC",
                market="Lse",
                etoro_instrument_id=123,
                interval_minutes=1,
                start=datetime(2026, 8, 23, 7, minute, tzinfo=UTC),
                open=price,
                high=price,
                low=price,
                close=price,
                source_minutes=1,
            )

        result = bridge.add(
            event=EVENT,
            tracked=tracked_mixed_case,
            reference_candles=(candle_for_tracked(4, "100"),),
            reaction_candle=candle_for_tracked(5, "96"),
        )

        self.assertIsNotNone(result)

    def test_genuinely_different_market_still_fails_closed(self) -> None:
        bridge = MarketEventReactionBridge()
        tracked_other_market = TrackedEtoroInstrument(
            tracked_instrument_id="tracked-1",
            instrument="ABC",
            market="NASDAQ",
            etoro_instrument_id=123,
            etoro_symbol="ABC.L",
            etoro_display_name="ABC plc",
        )

        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            bridge.add(
                event=EVENT,
                tracked=tracked_other_market,
                reference_candles=(_candle(4, "100"),),
                reaction_candle=_candle(5, "96"),
            )

    def test_no_reference_returns_none_through_bridge(self) -> None:
        bridge = MarketEventReactionBridge()

        result = bridge.add(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=(_candle(5, "100"),),
            reaction_candle=_candle(6, "98"),
        )

        self.assertIsNone(result)

    def test_monitoring_profile_selects_one_minute_before_thirty_minutes(self) -> None:
        bridge = MarketEventReactionBridge()

        result = bridge.add_for_observation(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candles=(
                _candle(9, "98", interval=1),
                _candle(5, "97", interval=5),
            ),
            observed_at=EVENT.event_at + timedelta(minutes=5),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reaction.tracked_reaction.reaction.interval_minutes, 1)
        self.assertEqual(result.reference.interval_minutes, 1)

    def test_monitoring_profile_switches_to_five_minutes_with_stable_one_minute_reference(self) -> None:
        bridge = MarketEventReactionBridge()
        references = (_candle(4, "100"),)

        first = bridge.add_for_observation(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=references,
            reaction_candles=(_candle(10, "98", interval=1),),
            observed_at=EVENT.event_at + timedelta(minutes=6),
        )
        second = bridge.add_for_observation(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=references,
            reaction_candles=(
                _candle(39, "96", interval=1),
                _candle(35, "95", interval=5),
            ),
            observed_at=EVENT.event_at + timedelta(minutes=35),
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first.reference.source_candle_start, references[0].start)
        self.assertEqual(second.reference.source_candle_start, references[0].start)
        self.assertEqual(first.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(second.reference.baseline.reference_price, Decimal("100"))
        self.assertEqual(second.reaction.tracked_reaction.reaction.interval_minutes, 5)

    def test_monitoring_profile_switches_to_fifteen_minutes(self) -> None:
        bridge = MarketEventReactionBridge()

        result = bridge.add_for_observation(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candles=(
                _candle(44, "97", interval=1, hour=9),
                _candle(40, "96", interval=5, hour=9),
                _candle(30, "94", interval=15, hour=9),
            ),
            observed_at=EVENT.event_at + timedelta(minutes=160),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reaction.tracked_reaction.reaction.interval_minutes, 15)
        self.assertEqual(result.reference.interval_minutes, 1)

    def test_monitoring_profile_returns_none_when_active_interval_did_not_close(self) -> None:
        bridge = MarketEventReactionBridge()

        result = bridge.add_for_observation(
            event=EVENT,
            tracked=TRACKED,
            reference_candles=(_candle(4, "100"),),
            reaction_candles=(_candle(35, "95", interval=5),),
            observed_at=EVENT.event_at + timedelta(minutes=10),
        )

        self.assertIsNone(result)

    def test_monitoring_profile_fails_closed_on_ambiguous_active_candle(self) -> None:
        bridge = MarketEventReactionBridge()

        with self.assertRaisesRegex(ValueError, "ambiguous active reaction candle"):
            bridge.add_for_observation(
                event=EVENT,
                tracked=TRACKED,
                reference_candles=(_candle(4, "100"),),
                reaction_candles=(
                    _candle(9, "98", interval=1),
                    _candle(10, "97", interval=1),
                ),
                observed_at=EVENT.event_at + timedelta(minutes=6),
            )


if __name__ == "__main__":
    unittest.main()
