from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable

from trading_system.brokers.etoro_demo import EtoroDemoBroker
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.etoro_session_observability import trading_session_state_from_etoro_update
from trading_system.session_execution_gate import evaluate_session_execution
from trading_system.trading_session_state import TradingSessionState


DEFAULT_SESSION_MAX_AGE = timedelta(seconds=30)


async def _first_explicit_session_state(
    provider: EtoroMarketDataProvider,
    *,
    broker: EtoroDemoBroker,
    max_age: timedelta,
    now: Callable[[], datetime],
) -> TradingSessionState:
    async for update in provider.stream_instrument(broker.instrument_id, reconnect=False):
        state = trading_session_state_from_etoro_update(
            update,
            now=now(),
            max_age=max_age,
            allow_extended_hours=False,
        )
        if state is not None:
            return state
    raise RuntimeError("eToro session stream ended without explicit session evidence")


def verify_etoro_demo_session_execution(
    provider: EtoroMarketDataProvider,
    broker: EtoroDemoBroker,
    *,
    max_age: timedelta = DEFAULT_SESSION_MAX_AGE,
    timeout_seconds: float | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    """Fail closed unless fresh direct eToro session evidence permits this PAPER order.

    The check is intended for the worker's immediate pre-reservation preflight.
    Extended-hours policy remains disabled, and EtoroDemoBroker still advertises
    no verified extended-hours order capability.
    """
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    timeout = broker.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("timeout_seconds must be positive")

    try:
        state = asyncio.run(
            asyncio.wait_for(
                _first_explicit_session_state(
                    provider,
                    broker=broker,
                    max_age=max_age,
                    now=now,
                ),
                timeout=timeout,
            )
        )
    except TimeoutError as exc:
        raise RuntimeError("eToro session evidence timed out before PAPER broker attempt") from exc

    decision = evaluate_session_execution(session=state, broker=broker)
    if not decision.allowed:
        raise RuntimeError(f"eToro PAPER session preflight blocked execution: {decision.reason}")
