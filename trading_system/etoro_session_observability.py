from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.trading_session_state import TradingSessionState


def trading_session_state_from_etoro_update(
    update: EtoroMarketUpdate,
    *,
    now: datetime,
    max_age: timedelta,
    allow_extended_hours: bool,
) -> TradingSessionState | None:
    """Map one explicit eToro stream update into the generic session state.

    Missing or contradictory broker/session evidence returns ``None`` so callers
    fail closed. Extended-session capability is asserted only when the eToro API
    itself says the broker market is open while the underlying exchange is closed.
    No symbol, exchange-label, market-label, or UI inference is used.
    """
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if update.timestamp is None:
        return None
    if update.timestamp.tzinfo is None or update.timestamp.utcoffset() is None:
        return None
    if update.is_market_open is None or update.is_exchange_open is None:
        return None

    observed_at = update.timestamp.astimezone(timezone.utc)
    current_at = now.astimezone(timezone.utc)
    age = current_at - observed_at
    market_data_fresh = timedelta(0) <= age <= max_age

    # The exchange cannot be considered safely executable through this broker
    # if eToro explicitly says its market is closed despite the exchange flag.
    if update.is_exchange_open and not update.is_market_open:
        return None

    return TradingSessionState(
        exchange_session_open=update.is_exchange_open,
        broker_extended_session_available=(
            update.is_market_open and not update.is_exchange_open
        ),
        allow_extended_hours=allow_extended_hours,
        market_data_fresh=market_data_fresh,
    )
