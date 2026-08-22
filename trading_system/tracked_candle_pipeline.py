from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.multi_minute_candles import MultiMinuteCandle, MultiMinuteCandleBuilder
from trading_system.one_minute_candles import OneMinuteCandle, OneMinuteCandleBuilder
from trading_system.tracked_etoro_live import TrackedEtoroMarketUpdate


@dataclass(frozen=True)
class TrackedMarketCandle:
    """A closed OHLC candle tied back to one tracked-instrument identity."""

    tracked_instrument_id: str
    instrument: str
    market: str
    etoro_instrument_id: int
    interval_minutes: int
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source_minutes: int


@dataclass
class _TrackedCandleState:
    instrument: str
    market: str
    etoro_instrument_id: int
    one_minute: OneMinuteCandleBuilder
    five_minute: MultiMinuteCandleBuilder
    fifteen_minute: MultiMinuteCandleBuilder


class TrackedCandlePipeline:
    """Build closed 1m/5m/15m candles from tracked eToro market updates.

    State is isolated by ``tracked_instrument_id``. The first valid update for a
    tracked identity fixes its instrument/market/eToro identity for the lifetime
    of this pipeline instance; later attempts to reuse that tracked ID with a
    different identity raise instead of silently mixing candle histories.

    Only closed candles are emitted. The pipeline never calls builder ``flush``
    methods during normal operation, so partial candles are not exposed as if
    they were closed market data.
    """

    def __init__(self) -> None:
        self._states: dict[str, _TrackedCandleState] = {}

    @staticmethod
    def _from_one_minute(item: TrackedEtoroMarketUpdate, candle: OneMinuteCandle) -> TrackedMarketCandle:
        return TrackedMarketCandle(
            tracked_instrument_id=item.tracked_instrument_id,
            instrument=item.instrument,
            market=item.market,
            etoro_instrument_id=item.etoro_instrument_id,
            interval_minutes=1,
            start=candle.start,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            source_minutes=1,
        )

    @staticmethod
    def _from_multi_minute(item: TrackedEtoroMarketUpdate, candle: MultiMinuteCandle) -> TrackedMarketCandle:
        return TrackedMarketCandle(
            tracked_instrument_id=item.tracked_instrument_id,
            instrument=item.instrument,
            market=item.market,
            etoro_instrument_id=item.etoro_instrument_id,
            interval_minutes=candle.interval_minutes,
            start=candle.start,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            source_minutes=candle.source_minutes,
        )

    def _state_for(self, item: TrackedEtoroMarketUpdate) -> _TrackedCandleState:
        state = self._states.get(item.tracked_instrument_id)
        if state is None:
            state = _TrackedCandleState(
                instrument=item.instrument,
                market=item.market,
                etoro_instrument_id=item.etoro_instrument_id,
                one_minute=OneMinuteCandleBuilder(item.etoro_instrument_id),
                five_minute=MultiMinuteCandleBuilder(item.etoro_instrument_id, 5),
                fifteen_minute=MultiMinuteCandleBuilder(item.etoro_instrument_id, 15),
            )
            self._states[item.tracked_instrument_id] = state
            return state

        if (
            state.instrument != item.instrument
            or state.market != item.market
            or state.etoro_instrument_id != item.etoro_instrument_id
        ):
            raise ValueError("tracked instrument identity changed")
        return state

    def add(self, item: TrackedEtoroMarketUpdate) -> tuple[TrackedMarketCandle, ...]:
        """Consume one tracked market update and return newly closed candles."""
        if item.update.instrument_id != item.etoro_instrument_id:
            return ()

        state = self._state_for(item)
        output: list[TrackedMarketCandle] = []
        for minute in state.one_minute.add(item.update):
            output.append(self._from_one_minute(item, minute))
            for candle in state.five_minute.add(minute):
                output.append(self._from_multi_minute(item, candle))
            for candle in state.fifteen_minute.add(minute):
                output.append(self._from_multi_minute(item, candle))
        return tuple(output)
