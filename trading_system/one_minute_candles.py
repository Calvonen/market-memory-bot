from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    field. Updates without a timestamp or usable price are ignored.
    """

    def __init__(self, instrument_id: int) -> None:
        self.instrument_id = instrument_id
        self._current_start: datetime | None = None
        self._open: Decimal | None = None
        self._high: Decimal | None = None
        self._low: Decimal | None = None
        self._close: Decimal | None = None

    @staticmethod
    def _minute_start(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc).replace(second=0, microsecond=0)

    @staticmethod
    def _price(update: EtoroMarketUpdate) -> Decimal | None:
        if update.last_execution is not None:
            return update.last_execution
        if update.bid is not None and update.ask is not None:
            return (update.bid + update.ask) / Decimal("2")
        return update.bid if update.bid is not None else update.ask

    def _reset(self, minute_start: datetime, price: Decimal) -> None:
        self._current_start = minute_start
        self._open = price
        self._high = price
        self._low = price
        self._close = price

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
        """
        if update.instrument_id != self.instrument_id or update.timestamp is None:
            return ()
        price = self._price(update)
        if price is None:
            return ()

        minute_start = self._minute_start(update.timestamp)
        if self._current_start is None:
            self._reset(minute_start, price)
            return ()

        if minute_start < self._current_start:
            return ()

        if minute_start == self._current_start:
            assert self._high is not None and self._low is not None
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._close = price
            return ()

        closed = self._closed_candle()
        self._reset(minute_start, price)
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
        return candle
