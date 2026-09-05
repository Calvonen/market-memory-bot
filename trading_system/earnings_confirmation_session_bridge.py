from __future__ import annotations

from dataclasses import dataclass

from trading_system.trading_session_state import TradingSessionState


@dataclass(frozen=True)
class EarningsConfirmationSessionDecision:
    continue_confirmation: bool
    reason: str


def evaluate_earnings_confirmation_session(
    *,
    session: TradingSessionState,
) -> EarningsConfirmationSessionDecision:
    """Decide whether earnings confirmation may keep consuming broker evidence.

    Confirmation observability is intentionally separate from order execution
    authority. Fresh broker-session evidence may continue confirmation even when
    extended-hours orders are not enabled; the execution gate remains responsible
    for deciding whether an order may be attempted.
    """
    if not session.market_data_fresh:
        return EarningsConfirmationSessionDecision(False, "market_data_stale")

    if session.exchange_session_open:
        return EarningsConfirmationSessionDecision(True, "exchange_session")

    if session.broker_extended_session_available:
        return EarningsConfirmationSessionDecision(True, "broker_session")

    return EarningsConfirmationSessionDecision(False, "broker_session_unavailable")
