from __future__ import annotations

from datetime import UTC

from trading_system.market_event import MarketEvent
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.scanner_market_event_ingress import register_scanner_market_event
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.trend_monitoring_contract import TrendState, TrendTransition


def register_confirmed_scanner_trend_event(
    monitor: RegisteredMarketEventMonitor,
    tracked: TrackedEtoroInstrument,
    transition: TrendTransition,
) -> MarketEvent | None:
    """Promote only a confirmed canonical bullish/bearish trend change to scanner ingress.

    Confirmation remains owned by ``apply_trend_confirmation`` in the canonical
    Trend monitoring contract. This adapter deliberately does not inspect Market
    Memory similarity scores or invent a separate scanner threshold.

    A pending observation, duplicate candle, neutral transition, or otherwise
    unchanged Trend state produces no market event. The resulting scanner event
    remains metadata only; Strategy, Risk, trading-task creation and broker
    execution stay downstream of the canonical market-event workflow.
    """
    if not transition.changed:
        return None
    if transition.state not in {TrendState.BULLISH, TrendState.BEARISH}:
        return None

    event_at = transition.last_processed_candle_at
    if event_at is None:
        raise RuntimeError("confirmed trend transition is missing candle identity")
    if event_at.tzinfo is None or event_at.utcoffset() is None:
        raise RuntimeError(
            "confirmed trend transition candle identity must be timezone-aware"
        )

    event_at_utc = event_at.astimezone(UTC)
    event_id = (
        f"scanner-trend:{tracked.tracked_instrument_id}:"
        f"{transition.state.value}:{event_at_utc.isoformat()}"
    )
    title = f"Confirmed {transition.state.value} trend"

    return register_scanner_market_event(
        monitor,
        tracked,
        event_id=event_id,
        event_at=event_at_utc,
        title=title,
    )
