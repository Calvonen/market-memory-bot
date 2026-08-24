from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd


PRE_EVENT_MARKET_CONTEXT_SCHEMA_VERSION = 1
_REQUIRED_COLUMNS = ("Open", "High", "Low", "Close")


@dataclass(frozen=True)
class PreEventMarketContext:
    """Observation-only context from the last complete session before an event."""

    session_date: date
    previous_session_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    previous_close_price: Decimal
    session_return_pct: Decimal
    close_to_close_return_pct: Decimal
    schema_version: int = PRE_EVENT_MARKET_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("session_date", self.session_date),
            ("previous_session_date", self.previous_session_date),
        ):
            if isinstance(value, datetime) or not isinstance(value, date):
                raise ValueError(f"{name} must be a date")
        if self.previous_session_date >= self.session_date:
            raise ValueError("previous_session_date must be before session_date")
        if self.schema_version != PRE_EVENT_MARKET_CONTEXT_SCHEMA_VERSION:
            raise ValueError("unsupported pre-event market context schema_version")

        prices = {
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "close_price": self.close_price,
            "previous_close_price": self.previous_close_price,
        }
        for name, value in prices.items():
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")

        if self.high_price < max(self.open_price, self.close_price, self.low_price):
            raise ValueError("high_price is inconsistent with session OHLC")
        if self.low_price > min(self.open_price, self.close_price, self.high_price):
            raise ValueError("low_price is inconsistent with session OHLC")

        expected_session_return = (
            (self.close_price / self.open_price) - Decimal("1")
        ) * Decimal("100")
        expected_close_to_close = (
            (self.close_price / self.previous_close_price) - Decimal("1")
        ) * Decimal("100")
        if self.session_return_pct != expected_session_return:
            raise ValueError("session_return_pct does not match session open/close")
        if self.close_to_close_return_pct != expected_close_to_close:
            raise ValueError(
                "close_to_close_return_pct does not match previous/current close"
            )

    @property
    def close_to_close_direction(self) -> str:
        if self.close_to_close_return_pct > 0:
            return "up"
        if self.close_to_close_return_pct < 0:
            return "down"
        return "flat"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_date": self.session_date.isoformat(),
            "previous_session_date": self.previous_session_date.isoformat(),
            "open_price": str(self.open_price),
            "high_price": str(self.high_price),
            "low_price": str(self.low_price),
            "close_price": str(self.close_price),
            "previous_close_price": str(self.previous_close_price),
            "session_return_pct": str(self.session_return_pct),
            "close_to_close_return_pct": str(self.close_to_close_return_pct),
            "close_to_close_direction": self.close_to_close_direction,
        }


def _positive_decimal(value: object, *, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite and positive") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field_name} must be finite and positive")
    return result


def pre_event_market_context(
    daily_ohlcv: pd.DataFrame,
    *,
    event_trading_date: date,
) -> PreEventMarketContext | None:
    """Build context from the last two complete market-session rows.

    The index contract is deliberately strict: it must contain timezone-naive
    midnight labels whose calendar dates already represent the exchange's
    session dates. Market timezone/session-calendar conversion belongs to the
    acquisition layer, before this pure selector is called.
    """

    if isinstance(event_trading_date, datetime) or not isinstance(event_trading_date, date):
        raise ValueError("event_trading_date must be a date")
    missing = [column for column in _REQUIRED_COLUMNS if column not in daily_ohlcv.columns]
    if missing:
        raise ValueError(f"daily_ohlcv is missing required columns: {missing}")
    if not isinstance(daily_ohlcv.index, pd.DatetimeIndex):
        raise ValueError("daily_ohlcv index must be a DatetimeIndex")
    if daily_ohlcv.index.tz is not None:
        raise ValueError("daily_ohlcv index must use timezone-naive market session dates")
    if any(timestamp != timestamp.normalize() for timestamp in daily_ohlcv.index):
        raise ValueError("daily_ohlcv index must contain midnight market session dates")

    session_dates = [timestamp.date() for timestamp in daily_ohlcv.index]
    if len(session_dates) != len(set(session_dates)):
        raise ValueError("daily_ohlcv contains duplicate market session dates")

    completed = daily_ohlcv.loc[
        [session_date < event_trading_date for session_date in session_dates]
    ].sort_index()
    if len(completed) < 2:
        return None

    previous_row = completed.iloc[-2]
    session_row = completed.iloc[-1]
    previous_session_date = completed.index[-2].date()
    session_date = completed.index[-1].date()

    open_price = _positive_decimal(session_row["Open"], field_name="open_price")
    high_price = _positive_decimal(session_row["High"], field_name="high_price")
    low_price = _positive_decimal(session_row["Low"], field_name="low_price")
    close_price = _positive_decimal(session_row["Close"], field_name="close_price")
    previous_close_price = _positive_decimal(
        previous_row["Close"], field_name="previous_close_price"
    )
    session_return_pct = ((close_price / open_price) - Decimal("1")) * Decimal("100")
    close_to_close_return_pct = (
        (close_price / previous_close_price) - Decimal("1")
    ) * Decimal("100")

    return PreEventMarketContext(
        session_date=session_date,
        previous_session_date=previous_session_date,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        previous_close_price=previous_close_price,
        session_return_pct=session_return_pct,
        close_to_close_return_pct=close_to_close_return_pct,
    )
