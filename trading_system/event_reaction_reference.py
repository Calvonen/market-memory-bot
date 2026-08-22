from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_system.event_market_reaction import EventMarketReactionBaseline
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


@dataclass(frozen=True)
class EventReactionReferenceSelection:
    """One deterministic pre-event reference candle and the baseline derived from it."""

    baseline: EventMarketReactionBaseline
    event_at: datetime
    interval_minutes: int
    source_candle_start: datetime


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def select_event_reaction_reference(
    *,
    event_id: str,
    event_at: datetime,
    tracked: TrackedEtoroInstrument,
    candles: tuple[TrackedMarketCandle, ...],
    interval_minutes: int = 1,
) -> EventReactionReferenceSelection | None:
    """Select the latest complete candle that ended no later than the event.

    The caller supplies the event timestamp and candidate closed candles. This
    helper does not discover events or fetch market data. It filters candidates
    to the requested tracked identity and interval, requires a complete source
    window, and uses the latest candle whose *end* is ``<= event_at``. Using the
    candle end rather than its start prevents post-event prices from leaking
    into the reference baseline.

    ``None`` means no eligible pre-event candle was available. Invalid caller
    metadata fails closed with ``ValueError``.
    """
    if not event_id.strip():
        raise ValueError("event_id must not be blank")
    if not _is_aware(event_at):
        raise ValueError("event_at must be timezone-aware")
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")

    eligible: list[TrackedMarketCandle] = []
    for candle in candles:
        if (
            candle.tracked_instrument_id != tracked.tracked_instrument_id
            or candle.instrument != tracked.instrument
            or candle.market != tracked.market
            or candle.etoro_instrument_id != tracked.etoro_instrument_id
        ):
            continue
        if candle.interval_minutes != interval_minutes:
            continue
        if candle.source_minutes != candle.interval_minutes:
            continue
        if not _is_aware(candle.start):
            raise ValueError("candle start must be timezone-aware")

        candle_end = candle.start + timedelta(minutes=candle.interval_minutes)
        if candle_end > event_at:
            continue

        if not candle.close.is_finite() or candle.close <= 0:
            raise ValueError("eligible candle close must be finite and positive")

        eligible.append(candle)

    if not eligible:
        return None

    latest_start = max(candle.start for candle in eligible)
    latest = [candle for candle in eligible if candle.start == latest_start]
    if len(latest) != 1:
        raise ValueError("ambiguous pre-event reference candle")

    source = latest[0]
    baseline = EventMarketReactionBaseline(
        event_id=event_id,
        tracked_instrument_id=source.tracked_instrument_id,
        instrument=source.instrument,
        market=source.market,
        etoro_instrument_id=source.etoro_instrument_id,
        reference_price=source.close,
    )
    return EventReactionReferenceSelection(
        baseline=baseline,
        event_at=event_at,
        interval_minutes=interval_minutes,
        source_candle_start=source.start,
    )
