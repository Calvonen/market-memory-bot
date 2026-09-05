from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradingSessionState:
    """Explicit broker/session observability inputs for a potential execution.

    This is a data model only. It deliberately does not infer broker extended
    trading support from an exchange, symbol, market label, or UI observation.
    """

    exchange_session_open: bool
    broker_extended_session_available: bool
    allow_extended_hours: bool
    market_data_fresh: bool

    @property
    def execution_observable(self) -> bool:
        """Return whether the intended execution session is observable safely."""
        if self.exchange_session_open:
            return self.market_data_fresh
        return (
            self.broker_extended_session_available
            and self.allow_extended_hours
            and self.market_data_fresh
        )

    @property
    def uses_extended_hours(self) -> bool:
        """Return whether execution would rely on broker extended hours."""
        return (
            not self.exchange_session_open
            and self.broker_extended_session_available
            and self.allow_extended_hours
            and self.market_data_fresh
        )
