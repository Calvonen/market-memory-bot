from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from trading_system.tracked_candle_pipeline import TrackedMarketCandle


DEFAULT_FLAT_THRESHOLD_PCT = Decimal("0.25")


class ReactionDirection(str, Enum):
    NEGATIVE = "negative"
    FLAT = "flat"
    POSITIVE = "positive"


@dataclass(frozen=True)
class MarketReactionSnapshot:
    """Deterministic reaction of one closed candle versus a caller-supplied reference price."""

    tracked_instrument_id: str
    instrument: str
    market: str
    etoro_instrument_id: int
    interval_minutes: int
    candle_start: object
    reference_price: Decimal
    close_price: Decimal
    return_pct: Decimal
    direction: ReactionDirection
    source_minutes: int
    complete_window: bool


class MarketReactionEngine:
    """Classify closed market candles relative to an explicit reference price.

    The engine deliberately does not decide what the reference price means. A
    future event layer can supply a pre-event close, pre-release baseline, or
    another reviewed anchor. This foundation only measures the signed reaction
    and labels its direction; negative reactions are observations, not vetoes or
    trading decisions.

    Sparse 5m/15m aggregates are still measurable, but ``complete_window`` is
    false when fewer source 1-minute candles contributed than the interval size.
    """

    def __init__(self, flat_threshold_pct: Decimal = DEFAULT_FLAT_THRESHOLD_PCT) -> None:
        if not flat_threshold_pct.is_finite() or flat_threshold_pct < 0:
            raise ValueError("flat_threshold_pct must be finite and non-negative")
        self.flat_threshold_pct = flat_threshold_pct

    def analyze(
        self,
        candle: TrackedMarketCandle,
        *,
        reference_price: Decimal,
    ) -> MarketReactionSnapshot:
        if not reference_price.is_finite() or reference_price <= 0:
            raise ValueError("reference_price must be finite and positive")
        if not candle.close.is_finite():
            raise ValueError("candle close must be finite")
        if candle.interval_minutes <= 0:
            raise ValueError("candle interval must be positive")
        if candle.source_minutes < 1 or candle.source_minutes > candle.interval_minutes:
            raise ValueError("source_minutes must be within the candle interval")

        return_pct = ((candle.close - reference_price) / reference_price) * Decimal("100")
        if return_pct > self.flat_threshold_pct:
            direction = ReactionDirection.POSITIVE
        elif return_pct < -self.flat_threshold_pct:
            direction = ReactionDirection.NEGATIVE
        else:
            direction = ReactionDirection.FLAT

        return MarketReactionSnapshot(
            tracked_instrument_id=candle.tracked_instrument_id,
            instrument=candle.instrument,
            market=candle.market,
            etoro_instrument_id=candle.etoro_instrument_id,
            interval_minutes=candle.interval_minutes,
            candle_start=candle.start,
            reference_price=reference_price,
            close_price=candle.close,
            return_pct=return_pct,
            direction=direction,
            source_minutes=candle.source_minutes,
            complete_window=candle.source_minutes == candle.interval_minutes,
        )
