from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketUpdate


@dataclass(frozen=True)
class OneMinuteCandle:
    instrument_id: int
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class OneMinuteCandleBuilder:
    """Build closed 1-minute OHLC candles from eToro market updates.

    This deliberately uses price updates only. Volume is not inferred because
    the current eToro stream contract does not provide a trustworthy volume
    field. Updates without a timestamp or usable price are ignored, as are
    updates whose selected price is not finite (NaN/Infinity/-Infinity).

    Within the currently open minute, updates are tracked by event timestamp
    rather than arrival order: `open` always reflects the earliest timestamp
    seen so far in the minute and `close` always reflects the latest, even if
    a later-arriving update carries an earlier or later timestamp than one
    already processed. `high`/`low` consider every valid price in the minute
    regardless of arrival order.
    """

    def __init__(self, instrument_id: int) -> None:
        self.instrument_id = instrument_id
        self._current_start: datetime | None = None
        self._open: Decimal | None = None
        self._high: Decimal | None = None
        self._low: Decimal | None = None
        self._close: Decimal | None = None
        self._earliest_ts: datetime | None = None
        self._latest_ts: datetime | None = None

    @staticmethod
    def _to_utc(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    @classmethod
    def _minute_start(cls, timestamp: datetime) -> datetime:
        return cls._to_utc(timestamp).replace(second=0, microsecond=0)

    @staticmethod
    def _price(update: EtoroMarketUpdate) -> Decimal | None:
        if update.last_execution is not None:
            return update.last_execution
        if update.bid is not None and update.ask is not None:
            return (update.bid + update.ask) / Decimal("2")
        return update.bid if update.bid is not None else update.ask

    def _reset(self, minute_start: datetime, event_ts: datetime, price: Decimal) -> None:
        self._current_start = minute_start
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._earliest_ts = event_ts
        self._latest_ts = event_ts

    def _closed_candle(self) -> OneMinuteCandle:
        assert self._current_start is not None
        assert self._open is not None
        assert self._high is not None
        assert self._low is not None
        assert self._close is not None
        return OneMinuteCandle(
            instrument_id=self.instrument_id,
            start=self._current_start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
        )

    def add(self, update: EtoroMarketUpdate) -> tuple[OneMinuteCandle, ...]:
        """Consume one update and return any candle closed by it.

        Late updates for an already-closed minute are ignored. If time jumps
        over one or more empty minutes, no synthetic candles are created; only
        the prior real candle is closed and the new real minute is started.
        Within the still-open minute, out-of-order arrivals are reconciled by
        event timestamp: `open`/`close` track the earliest/latest timestamp
        seen, not arrival order.
        """
        if update.instrument_id != self.instrument_id or update.timestamp is None:
            return ()
        price = self._price(update)
        if price is None or not price.is_finite():
            return ()

        event_ts = self._to_utc(update.timestamp)
        minute_start = event_ts.replace(second=0, microsecond=0)
        if self._current_start is None:
            self._reset(minute_start, event_ts, price)
            return ()

        if minute_start < self._current_start:
            return ()

        if minute_start == self._current_start:
            assert self._high is not None and self._low is not None
            assert self._earliest_ts is not None and self._latest_ts is not None
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            if event_ts <= self._earliest_ts:
                self._earliest_ts = event_ts
                self._open = price
            if event_ts >= self._latest_ts:
                self._latest_ts = event_ts
                self._close = price
            return ()

        closed = self._closed_candle()
        self._reset(minute_start, event_ts, price)
        return (closed,)

    def flush(self) -> OneMinuteCandle | None:
        """Return the current partial candle and clear the builder.

        Intended for controlled shutdown/tests. Runtime callers should normally
        persist/use only candles returned by `add`, which are closed by a later
        market update.
        """
        if self._current_start is None:
            return None
        candle = self._closed_candle()
        self._current_start = None
        self._open = self._high = self._low = self._close = None
        self._earliest_ts = self._latest_ts = None
        return candle
