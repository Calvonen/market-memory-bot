from __future__ import annotations

from dataclasses import dataclass

from trading_system.brokers.base import Broker
from trading_system.trading_session_state import TradingSessionState


@dataclass(frozen=True)
class SessionExecutionDecision:
    allowed: bool
    reason: str


def evaluate_session_execution(
    *,
    session: TradingSessionState,
    broker: Broker,
) -> SessionExecutionDecision:
    """Combine session observability with explicit broker order capability."""
    if not session.execution_observable:
        return SessionExecutionDecision(False, "session_not_observable")

    if not session.uses_extended_hours:
        return SessionExecutionDecision(True, "regular_session")

    if not broker.supports_extended_hours_orders:
        return SessionExecutionDecision(False, "extended_hours_order_unsupported")

    return SessionExecutionDecision(True, "extended_hours")
