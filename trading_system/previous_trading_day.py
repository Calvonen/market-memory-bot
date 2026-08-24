from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd


@dataclass(frozen=True)
class PreviousTradingDaySnapshot:
    """Observation-only snapshot of the last completed trading day before an event.

    The return is close-to-close: the event's previous trading-day close compared
    with the close of the trading day before it. This deliberately does not make
    a bullish/bearish trading judgement; it only records persisted-price-ready
    inputs for a later evaluation step.
    """

    trading_date: date
    prior_trading_date: date
    prior_close_price: Decimal
    close_price: Decimal
    return_pct: Decimal
    direction: str


def _positive_decimal(value: object, *, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite positive number") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field_name} must be a finite positive number")
    return result


def previous_trading_day_snapshot(
    daily_ohlcv: pd.DataFrame,
    *,
    event_trading_date: date,
) -> PreviousTradingDaySnapshot | None:
    """Return the last completed daily close-to-close move before the event date.

    ``event_trading_date`` is intentionally supplied by the caller so market
    timezone/session-calendar resolution stays outside this pure selector.
    Weekends and holidays are handled naturally by selecting actual rows rather
    than subtracting calendar days. At least two completed sessions are required
    because a close-to-close return cannot otherwise be computed.
    """

    if "Close" not in daily_ohlcv.columns:
        raise ValueError("daily_ohlcv is missing Close")
    if not isinstance(daily_ohlcv.index, pd.DatetimeIndex):
        raise ValueError("daily_ohlcv index must be a DatetimeIndex")

    completed = daily_ohlcv.loc[
        [timestamp.date() < event_trading_date for timestamp in daily_ohlcv.index]
    ].sort_index()
    if len(completed) < 2:
        return None

    prior_row = completed.iloc[-2]
    latest_row = completed.iloc[-1]
    prior_timestamp = completed.index[-2]
    latest_timestamp = completed.index[-1]

    prior_close = _positive_decimal(prior_row["Close"], field_name="prior close")
    close = _positive_decimal(latest_row["Close"], field_name="close")
    return_pct = ((close / prior_close) - Decimal("1")) * Decimal("100")

    if return_pct > 0:
        direction = "up"
    elif return_pct < 0:
        direction = "down"
    else:
        direction = "flat"

    return PreviousTradingDaySnapshot(
        trading_date=latest_timestamp.date(),
        prior_trading_date=prior_timestamp.date(),
        prior_close_price=prior_close,
        close_price=close,
        return_pct=return_pct,
        direction=direction,
    )
