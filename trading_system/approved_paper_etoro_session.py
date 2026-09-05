from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from trading_system.etoro_market_data import EtoroMarketDataProvider, EtoroMarketUpdate
from trading_system.etoro_session_observability import trading_session_state_from_etoro_update
from trading_system.trading_session_state import TradingSessionState


async def _first_explicit_session_update(
    provider: EtoroMarketDataProvider,
    *,
    instrument_id: int,
) -> EtoroMarketUpdate:
    async for update in provider.stream_instrument(instrument_id, reconnect=False):
        if (
            update.timestamp is not None
            and update.is_market_open is not None
            and update.is_exchange_open is not None
        ):
            return update
    raise RuntimeError("eToro session stream ended without explicit session evidence")


def read_etoro_session_state(
    provider: EtoroMarketDataProvider,
    *,
    instrument_id: int,
    timeout_seconds: float,
    max_age_seconds: float,
    allow_extended_hours: bool,
) -> TradingSessionState:
    """Read one explicit, fresh eToro session observation or fail closed."""
    if instrument_id <= 0:
        raise ValueError("instrument_id must be positive")
    if timeout_seconds <= 0 or max_age_seconds <= 0:
        raise ValueError("session timeout and max age must be positive")

    try:
        update = asyncio.run(
            asyncio.wait_for(
                _first_explicit_session_update(provider, instrument_id=instrument_id),
                timeout=timeout_seconds,
            )
        )
    except TimeoutError as exc:
        raise RuntimeError("timed out waiting for explicit eToro session evidence") from exc

    state = trading_session_state_from_etoro_update(
        update,
        now=datetime.now(UTC),
        max_age=timedelta(seconds=max_age_seconds),
        allow_extended_hours=allow_extended_hours,
    )
    if state is None or not state.market_data_fresh:
        raise RuntimeError("eToro session evidence is missing, contradictory, or stale")
    return state
