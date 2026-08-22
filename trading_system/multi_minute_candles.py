from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from trading_system.one_minute_candles import OneMinuteCandle


SUPPORTED_INTERVALS = (5, 15)


@dataclass(frozen=True)
class MultiMinuteCandle:
    instrument_id: int
    interval_minutes: int
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source_minutes: int


class MultiMinuteCandleBuilder:
    """Aggregate closed 1-minute candles into aligned 5m/15m OHLC candles.

    Only real 1-minute candles are consumed; missing minutes are not synthesized.
    `source_minutes` therefore records how many actual 1-minute candles contributed
    to each aggregate so downstream logic can distinguish complete from sparse
    windows.
    """

    def __init__(self, instrument_id: int, interval_minutes: int) -> None:
        if interval_minutes not in SUPPORTED_INTERVALS:
            raise ValueError(f"unsupported interval_minutes: {interval_minutes}")
        self.instrument_id = instrument_id
        self.interval_minutes = interval_minutes
        self._current_start: datetime | None = None
        self._open: Decimal | None = None
        self._high: Decimal | None = None
        self._low: Decimal | None = None
        self._close: Decimal | None = None
        self._earliest_minute: datetime | None = None
        self._latest_minute: datetime | None = None
        self._source_minutes = 0

    @staticmethod
    def _to_utc(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    def _bucket_start(self, timestamp: datetime) -> datetime:
        timestamp = self._to_utc(timestamp).replace(second=0, microsecond=0)
        minute = timestamp.minute - (timestamp.minute % self.interval_minutes)
        return timestamp.replace(minute=minute)

    def _reset(self, bucket_start: datetime, candle: OneMinuteCandle, minute_start: datetime) -> None:
        self._current_start = bucket_start
        self._open = candle.open
        self._high = candle.high
        self._low = candle.low
        self._close = candle.close
        self._earliest_minute = minute_start
        self._latest_minute = minute_start
        self._source_minutes = 1

    def _closed_candle(self) -> MultiMinuteCandle:
        assert self._current_start is not None
        assert self._open is not None
        assert self._high is not None
        assert self._low is not None
        assert self._close is not None
        return MultiMinuteCandle(
            instrument_id=self.instrument_id,
            interval_minutes=self.interval_minutes,
            start=self._current_start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            source_minutes=self._source_minutes,
        )

    def add(self, candle: OneMinuteCandle) -> tuple[MultiMinuteCandle, ...]:
        if candle.instrument_id != self.instrument_id:
            return ()

        minute_start = self._to_utc(candle.start).replace(second=0, microsecond=0)
        bucket_start = self._bucket_start(minute_start)

        if self._current_start is None:
            self._reset(bucket_start, candle, minute_start)
            return ()

        if bucket_start < self._current_start:
            return ()

        if bucket_start == self._current_start:
            assert self._high is not None and self._low is not None
            assert self._earliest_minute is not None and self._latest_minute is not None
            self._high = max(self._high, candle.high)
            self._low = min(self._low, candle.low)
            self._source_minutes += 1
            if minute_start <= self._earliest_minute:
                self._earliest_minute = minute_start
                self._open = candle.open
            if minute_start >= self._latest_minute:
                self._latest_minute = minute_start
                self._close = candle.close
            return ()

        closed = self._closed_candle()
        self._reset(bucket_start, candle, minute_start)
        return (closed,)

    def flush(self) -> MultiMinuteCandle | None:
        """Return the current partial aggregate and clear the builder."""
        if self._current_start is None:
            return None
        candle = self._closed_candle()
        self._current_start = None
        self._open = self._high = self._low = self._close = None
        self._earliest_minute = self._latest_minute = None
        self._source_minutes = 0
        return candle
