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
    run_post_release_paper_from_tracked_event,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


EVENT_AT = datetime(2026, 9, 2, 19, 0, tzinfo=UTC)
ANCHOR = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)


class FakeResolver:
    def resolve(self, request):
        return ResolvedEtoroInstrument(
            instrument_id=123,
            symbol="EXM",
            display_name="Example",
            market="NASDAQ",
        )


def _snapshot(*, monitor_hours: float = 8.0):
    return {
        "schema_version": 1,
        "monitor_hours": monitor_hours,
        "reference_lead_seconds": 30.0,
        "max_wait_for_market_hours": 72.0,
        "reaction_stages": [
            {"start_after_minutes": 0, "interval_minutes": 1},
            {"start_after_minutes": 30, "interval_minutes": 5},
            {"start_after_minutes": 150, "interval_minutes": 15},
        ],
    }


def _event(*, tracking_config_snapshot=None) -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="event-1",
        tracked_instrument_id="instrument-1",
        calendar_event_id=None,
        company_name="Example",
        instrument="EXM",
        market="NASDAQ",
        source="manual_ir",
        external_key="fy26",
        kind="earnings",
        title="FY26 results",
        event_at=EVENT_AT,
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.MONITORING,
        resolved_etoro_instrument_id=123,
        resolved_etoro_symbol="EXM",
        resolved_etoro_display_name="Example",
        resolved_etoro_market="NASDAQ",
        resolution_armed_at=EVENT_AT - timedelta(minutes=2),
        resolution_armed_by="tracked-event-preflight",
        reference_price=Decimal("100"),
        reference_captured_at=EVENT_AT - timedelta(minutes=1),
        reference_kind="etoro_last_execution_pre_event_snapshot",
        reaction_anchor_at=ANCHOR,
        tracking_config_snapshot=tracking_config_snapshot,
    )


def _expectation() -> EventExpectation:
    return EventExpectation(
        event_id="tracked:event-1",
        instrument="EXM",
        event_name="FY26 results",
        scheduled_date=date(2026, 9, 2),
    )


def _task() -> CanonicalTradingTaskExecutionContext:
    return CanonicalTradingTaskExecutionContext(
        task_id="task-1",
        source_event_id="tracked:event-1",
        instrument="EXM",
        mode=TradingMode.PAPER,
    )


def _analysis() -> EventAnalysisPayload:
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


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity=10_000,
        cash=10_000,
        open_positions=0,
        spread_pct=0.1,
        volatility_pct=2.0,
    )


def _reaction(
    *,
    interval_minutes: int,
    candle_start: datetime,
    close_price: Decimal,
    direction: str,
) -> TrackedEventReactionRecord:
    reference = Decimal("100")
    return_pct = ((close_price - reference) / reference) * Decimal("100")
    return TrackedEventReactionRecord(
        tracked_market_event_id="event-1",
        interval_minutes=interval_minutes,
        candle_start=candle_start,
        reference_price=reference,
        close_price=close_price,
        return_pct=return_pct,
        direction=direction,
        evolution="continuation",
        observed_at=candle_start + timedelta(minutes=interval_minutes),
    )


def _flat_anchor() -> TrackedEventReactionRecord:
    return _reaction(
        interval_minutes=1,
        candle_start=ANCHOR,
        close_price=Decimal("100.10"),
        direction="flat",
    )


def _observation_close() -> TrackedEventReactionRecord:
    return _reaction(
        interval_minutes=1,
        candle_start=ANCHOR + timedelta(minutes=29),
        close_price=Decimal("100.10"),
        direction="flat",
    )


def _run(reactions, *, tracked_event=None):
    return run_post_release_paper_from_tracked_event(
        event=tracked_event or _event(tracking_config_snapshot=_snapshot()),
        expectation=_expectation(),
        analysis=_analysis(),
        reactions=reactions,
        portfolio=_portfolio(),
        resolver=FakeResolver(),
        trading_task=_task(),
    )


class PostReleaseConfirmationFallbackTests(unittest.TestCase):
    def test_first_post_30m_nonflat_5m_reaction_can_confirm(self) -> None:
        later = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=30),
            close_price=Decimal("102"),
            direction="positive",
        )
        expected = PostReleasePaperResult("waiting_confirmation", "delegated")
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper",
            return_value=expected,
        ) as run_paper:
            result = _run((_flat_anchor(), _observation_close(), later))

        self.assertIs(result, expected)
        self.assertEqual(run_paper.call_args.kwargs["confirmed_reaction_pct"], 2.0)

    def test_post_30m_selection_uses_first_nonflat_chronologically(self) -> None:
        first = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=30),
            close_price=Decimal("98"),
            direction="negative",
        )
        second = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=35),
            close_price=Decimal("103"),
            direction="positive",
        )
        expected = PostReleasePaperResult("waiting_confirmation", "delegated")
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper",
            return_value=expected,
        ) as run_paper:
            result = _run((second, _flat_anchor(), _observation_close(), first))

        self.assertIs(result, expected)
        self.assertEqual(run_paper.call_args.kwargs["confirmed_reaction_pct"], -2.0)

    def test_all_canonical_post_30m_reactions_flat_keep_waiting(self) -> None:
        later_flat = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=30),
            close_price=Decimal("100.10"),
            direction="flat",
        )
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = _run((_flat_anchor(), _observation_close(), later_flat))

        self.assertEqual(result.status, "waiting_confirmation")
        run_paper.assert_not_called()

    def test_reaction_after_persisted_monitor_horizon_cannot_confirm(self) -> None:
        later = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=30),
            close_price=Decimal("102"),
            direction="positive",
        )
        tracked_event = _event(tracking_config_snapshot=_snapshot(monitor_hours=0.5))
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = _run(
                (_flat_anchor(), _observation_close(), later),
                tracked_event=tracked_event,
            )

        self.assertEqual(result.status, "waiting_confirmation")
        run_paper.assert_not_called()

    def test_legacy_event_without_snapshot_cannot_gain_post_30m_authority(self) -> None:
        later = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=30),
            close_price=Decimal("102"),
            direction="positive",
        )
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = _run(
                (_flat_anchor(), _observation_close(), later),
                tracked_event=_event(tracking_config_snapshot=None),
            )

        self.assertEqual(result.status, "waiting_confirmation")
        run_paper.assert_not_called()

    def test_snapshot_profile_mismatch_fails_closed_before_pipeline(self) -> None:
        snapshot = _snapshot()
        snapshot["reaction_stages"] = [
            {"start_after_minutes": 0, "interval_minutes": 1},
            {"start_after_minutes": 45, "interval_minutes": 5},
        ]
        later = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=30),
            close_price=Decimal("102"),
            direction="positive",
        )
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            with self.assertRaisesRegex(ValueError, "differs from canonical reaction profile"):
                _run(
                    (_flat_anchor(), _observation_close(), later),
                    tracked_event=_event(tracking_config_snapshot=snapshot),
                )

        run_paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
