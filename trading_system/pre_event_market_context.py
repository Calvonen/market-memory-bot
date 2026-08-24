from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


PRE_EVENT_MARKET_CONTEXT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PreEventMarketContext:
    """Observation-only snapshot of the last complete session before an event.

    The raw OHLC values are the durable source data. ``reference_price`` is
    deliberately derived from the previous session close so a later worker can
    use the same persisted value when the exchange is still closed instead of
    depending on a zero/empty live ``lastExecution`` quote.

    ``late_session_return_pct`` is optional because the first small runtime PR
    may only have daily-session data available. A later acquisition layer can
    populate it from intraday candles without changing this v1 shape.
    """

    session_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    session_return_pct: Decimal
    late_session_return_pct: Decimal | None = None
    schema_version: int = PRE_EVENT_MARKET_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != PRE_EVENT_MARKET_CONTEXT_SCHEMA_VERSION:
            raise ValueError("unsupported pre-event market context schema_version")

        prices = {
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "close_price": self.close_price,
        }
        for name, value in prices.items():
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")

        if self.high_price < max(self.open_price, self.close_price, self.low_price):
            raise ValueError("high_price is inconsistent with session OHLC")
        if self.low_price > min(self.open_price, self.close_price, self.high_price):
            raise ValueError("low_price is inconsistent with session OHLC")

        if not self.session_return_pct.is_finite():
            raise ValueError("session_return_pct must be finite")
        if self.late_session_return_pct is not None and not self.late_session_return_pct.is_finite():
            raise ValueError("late_session_return_pct must be finite when present")

        expected_return = ((self.close_price / self.open_price) - Decimal("1")) * Decimal("100")
        if abs(expected_return - self.session_return_pct) > Decimal("0.000001"):
            raise ValueError("session_return_pct does not match session open/close")

    @property
    def reference_price(self) -> Decimal:
        """Canonical closed-market fallback reference for a pre-open event."""
        return self.close_price

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_date": self.session_date.isoformat(),
            "open_price": str(self.open_price),
            "high_price": str(self.high_price),
            "low_price": str(self.low_price),
            "close_price": str(self.close_price),
            "session_return_pct": str(self.session_return_pct),
            "late_session_return_pct": (
                str(self.late_session_return_pct)
                if self.late_session_return_pct is not None
                else None
            ),
        }
