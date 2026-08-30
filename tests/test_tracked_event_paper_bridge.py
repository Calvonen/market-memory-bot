from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.etoro_instrument_resolver import ResolvedEtoroInstrument
from trading_system.models import EventExpectation, PortfolioState, TradingMode
from trading_system.post_release_paper import PostReleasePaperResult
from trading_system.tracked_event_paper_bridge import (
    CanonicalTradingTaskExecutionContext,
    build_tracked_event_price_confirmation,
    canonical_release_event_id,
    run_post_release_paper_from_tracked_event,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


EVENT_AT = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
ANCHOR_AT = EVENT_AT + timedelta(hours=1)


class FakeResolver:
    def resolve(self, request):
        return ResolvedEtoroInstrument(
            instrument_id=123,
            symbol="EXM.ASX",
            display_name="Example Ltd",
            market="ASX",
        )


class MissingResolver:
    def resolve(self, request):
        return None


def event(
    *,
    anchor: datetime | None = ANCHOR_AT,
    reference_captured_at: datetime | None = EVENT_AT - timedelta(minutes=1),
    reference_kind: str | None = "etoro_last_execution_pre_event_snapshot",
    resolved_etoro_instrument_id: int | None = 123,
) -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="tracked-event-1",
        tracked_instrument_id="instrument-1",
        calendar_event_id=None,
        company_name="Example Ltd",
        instrument="EXM.ASX",
        market="ASX",
        source="manual_ir",
        external_key="example-fy26",
        kind="earnings",
        title="FY26 results",
        event_at=EVENT_AT,
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.MONITORING,
        resolved_etoro_instrument_id=resolved_etoro_instrument_id,
        resolved_etoro_symbol="EXM.ASX",
        resolved_etoro_display_name="Example Ltd",
        resolved_etoro_market="ASX",
        resolution_armed_at=EVENT_AT - timedelta(minutes=2),
        resolution_armed_by="tracked-event-preflight",
        reference_price=Decimal("10.00"),
        reference_captured_at=reference_captured_at,
        reference_kind=reference_kind,
        reaction_anchor_at=anchor,
    )


def expectation(*, event_id: str = "tracked:tracked-event-1") -> EventExpectation:
    return EventExpectation(
        event_id=event_id,
        instrument="EXM.ASX",
        event_name="FY26 results",
        scheduled_date=date(2026, 8, 31),
    )


def trading_task(
    *,
    task_id: str = "task-1",
    source_event_id: str = "tracked:tracked-event-1",
    instrument: str = "EXM.ASX",
    mode: TradingMode = TradingMode.PAPER,
) -> CanonicalTradingTaskExecutionContext:
    return CanonicalTradingTaskExecutionContext(
        task_id=task_id,
        source_event_id=source_event_id,
        instrument=instrument,
        mode=mode,
    )


def reaction(
    *,
    reference_price: Decimal = Decimal("10.00"),
    close_price: Decimal = Decimal("10.20"),
    return_pct: Decimal = Decimal("2.00"),
    direction: str = "positive",
    observed_at: datetime = ANCHOR_AT + timedelta(minutes=1),
) -> TrackedEventReactionRecord:
    return TrackedEventReactionRecord(
        tracked_market_event_id="tracked-event-1",
        interval_minutes=1,
        candle_start=ANCHOR_AT,
        reference_price=reference_price,
        close_price=close_price,
        return_pct=return_pct,
        direction=direction,
        evolution="initial",
        observed_at=observed_at,
    )


def analysis() -> EventAnalysisPayload:
    return EventAnalysisPayload(
        metrics=[],
        guidance_summary="guidance",
        management_summary="management",
        catalyst_direction="BULLISH",
        catalyst_score_0_25=20,
        fundamental_direction="BULLISH",
        fundamental_score_0_35=30,
        key_positive_surprises=[],
        key_negative_surprises=[],
        uncertainties=[],
        invalidation_flags=[],
        evidence_quotes=[],
    )


def portfolio() -> PortfolioState:
    return PortfolioState(
        equity=10_000,
        cash=10_000,
        open_positions=0,
        spread_pct=0.1,
        volatility_pct=2.0,
    )


class TrackedEventPaperBridgeTests(unittest.TestCase):
    def test_canonical_release_identity_uses_tracked_event_without_calendar(self) -> None:
        self.assertEqual(canonical_release_event_id(event()), "tracked:tracked-event-1")

    def test_selects_only_anchored_complete_one_minute_reaction(self) -> None:
        selected = build_tracked_event_price_confirmation(
            event=event(),
            expectation=expectation(),
            reactions=(reaction(),),
            resolver=FakeResolver(),
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.return_pct, Decimal("2.00"))
        self.assertEqual(selected.direction, "positive")

    def test_missing_anchor_returns_none_instead_of_falling_back(self) -> None:
        self.assertIsNone(
            build_tracked_event_price_confirmation(
                event=event(anchor=None),
                expectation=expectation(),
                reactions=(reaction(),),
                resolver=FakeResolver(),
            )
        )

    def test_expectation_identity_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "identity differs"):
            build_tracked_event_price_confirmation(
                event=event(),
                expectation=expectation(event_id="tracked:other"),
                reactions=(reaction(),),
                resolver=FakeResolver(),
            )

    def test_broker_identity_is_reresolved_and_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "instrument id differs"):
            build_tracked_event_price_confirmation(
                event=event(resolved_etoro_instrument_id=999),
                expectation=expectation(),
                reactions=(reaction(),),
                resolver=FakeResolver(),
            )

    def test_missing_broker_resolution_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "resolution failed"):
            build_tracked_event_price_confirmation(
                event=event(),
                expectation=expectation(),
                reactions=(reaction(),),
                resolver=MissingResolver(),
            )

    def test_reference_capture_after_event_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "captured after event"):
            build_tracked_event_price_confirmation(
                event=event(reference_captured_at=EVENT_AT + timedelta(seconds=1)),
                expectation=expectation(),
                reactions=(reaction(),),
                resolver=FakeResolver(),
            )

    def test_noncanonical_reference_kind_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "reference kind"):
            build_tracked_event_price_confirmation(
                event=event(reference_kind="daily_close"),
                expectation=expectation(),
                reactions=(reaction(),),
                resolver=FakeResolver(),
            )

    def test_reference_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "reference differs"):
            build_tracked_event_price_confirmation(
                event=event(),
                expectation=expectation(),
                reactions=(reaction(reference_price=Decimal("9.99")),),
                resolver=FakeResolver(),
            )

    def test_incomplete_one_minute_candle_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "before its candle completed"):
            build_tracked_event_price_confirmation(
                event=event(),
                expectation=expectation(),
                reactions=(reaction(observed_at=ANCHOR_AT + timedelta(seconds=59)),),
                resolver=FakeResolver(),
            )

    def test_return_must_match_stored_prices(self) -> None:
        with self.assertRaisesRegex(ValueError, "return differs"):
            build_tracked_event_price_confirmation(
                event=event(),
                expectation=expectation(),
                reactions=(
                    reaction(close_price=Decimal("9.80"), return_pct=Decimal("2.00")),
                ),
                resolver=FakeResolver(),
            )

    def test_direction_must_match_canonical_flat_threshold(self) -> None:
        with self.assertRaisesRegex(ValueError, "direction differs"):
            build_tracked_event_price_confirmation(
                event=event(),
                expectation=expectation(),
                reactions=(
                    reaction(
                        close_price=Decimal("10.01"),
                        return_pct=Decimal("0.10"),
                        direction="positive",
                    ),
                ),
                resolver=FakeResolver(),
            )

    def test_nonpaper_trading_task_fails_closed_before_pipeline(self) -> None:
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            with self.assertRaisesRegex(ValueError, "does not explicitly request PAPER"):
                run_post_release_paper_from_tracked_event(
                    event=event(),
                    expectation=expectation(),
                    analysis=analysis(),
                    reactions=(reaction(),),
                    portfolio=portfolio(),
                    resolver=FakeResolver(),
                    trading_task=trading_task(mode=TradingMode.LIVE),
                )
        run_paper.assert_not_called()

    def test_trading_task_event_mismatch_fails_closed_before_pipeline(self) -> None:
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            with self.assertRaisesRegex(ValueError, "event identity differs"):
                run_post_release_paper_from_tracked_event(
                    event=event(),
                    expectation=expectation(),
                    analysis=analysis(),
                    reactions=(reaction(),),
                    portfolio=portfolio(),
                    resolver=FakeResolver(),
                    trading_task=trading_task(source_event_id="tracked:other"),
                )
        run_paper.assert_not_called()

    def test_flat_reaction_waits_without_entering_paper_pipeline(self) -> None:
        flat = reaction(
            close_price=Decimal("10.01"),
            return_pct=Decimal("0.10"),
            direction="flat",
        )
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = run_post_release_paper_from_tracked_event(
                event=event(),
                expectation=expectation(),
                analysis=analysis(),
                reactions=(flat,),
                portfolio=portfolio(),
                resolver=FakeResolver(),
                trading_task=trading_task(),
            )
        self.assertEqual(result.status, "waiting_confirmation")
        run_paper.assert_not_called()

    def test_anchored_live_reaction_is_explicit_input_to_existing_paper_path(self) -> None:
        expected = PostReleasePaperResult("waiting_confirmation", "delegated")
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper",
            return_value=expected,
        ) as run_paper:
            result = run_post_release_paper_from_tracked_event(
                event=event(),
                expectation=expectation(),
                analysis=analysis(),
                reactions=(reaction(),),
                portfolio=portfolio(),
                resolver=FakeResolver(),
                trading_task=trading_task(),
            )
        self.assertIs(result, expected)
        self.assertEqual(run_paper.call_args.kwargs["confirmed_reaction_pct"], 2.0)

    def test_no_canonical_reaction_waits_without_daily_bar_fallback(self) -> None:
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = run_post_release_paper_from_tracked_event(
                event=event(),
                expectation=expectation(),
                analysis=analysis(),
                reactions=(),
                portfolio=portfolio(),
                resolver=FakeResolver(),
                trading_task=trading_task(),
            )
        self.assertEqual(result.status, "waiting_confirmation")
        run_paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
