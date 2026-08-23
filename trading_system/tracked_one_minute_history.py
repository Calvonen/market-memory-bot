from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from trading_system.tracked_candle_pipeline import TrackedMarketCandle


@dataclass(frozen=True)
class _TrackedHistoryIdentity:
    instrument: str
    market: str
    etoro_instrument_id: int


class TrackedOneMinuteHistory:
    """Keep a bounded recent history of complete 1m candles per tracked instrument.

    This history exists so an event discovered in the middle of trading can still
    use a deterministic pre-event 1m reference without starting a second candle
    stream. It does not discover events, build candles, analyze reactions, persist
    data, or create trading decisions.
    """

    def __init__(self, *, max_candles_per_instrument: int = 240) -> None:
        if max_candles_per_instrument <= 0:
            raise ValueError("max_candles_per_instrument must be positive")
        self.max_candles_per_instrument = max_candles_per_instrument
        self._histories: dict[str, deque[TrackedMarketCandle]] = {}
        self._identities: dict[str, _TrackedHistoryIdentity] = {}

    def _validate_identity(self, candle: TrackedMarketCandle) -> None:
        tracked_id = candle.tracked_instrument_id
        identity = _TrackedHistoryIdentity(
            instrument=candle.instrument,
            market=candle.market,
            etoro_instrument_id=candle.etoro_instrument_id,
        )
        existing = self._identities.get(tracked_id)
        if existing is None:
            self._identities[tracked_id] = identity
        elif existing != identity:
            raise ValueError("tracked candle history identity changed")

    def add(self, candles: tuple[TrackedMarketCandle, ...]) -> None:
        """Store complete 1m candles from an already-produced closed-candle batch."""
        for candle in candles:
            if candle.interval_minutes != 1:
                continue
            if candle.source_minutes != 1:
                raise ValueError("one-minute history requires complete one-minute candles")

            self._validate_identity(candle)
            history = self._histories.setdefault(
                candle.tracked_instrument_id,
                deque(maxlen=self.max_candles_per_instrument),
            )

            duplicate = next((item for item in history if item.start == candle.start), None)
            if duplicate is not None:
                if duplicate != candle:
                    raise ValueError("conflicting one-minute candle for existing start")
                continue

            if history and candle.start <= history[-1].start:
                raise ValueError("one-minute candles must arrive in strictly increasing order")
            history.append(candle)

    def candles_for(self, tracked_instrument_id: str) -> tuple[TrackedMarketCandle, ...]:
        tracked_id = tracked_instrument_id.strip()
        if not tracked_id:
            raise ValueError("tracked_instrument_id must not be blank")
        history = self._histories.get(tracked_id)
        return tuple(history) if history is not None else ()
