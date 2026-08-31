from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from trading_system.models import Direction, TradeProposal


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class BrokerOrder:
    order_id: str
    instrument: str
    direction: Direction
    quantity: int
    reference_price: float
    status: str
    created_at: datetime = field(default_factory=utc_now)
    # Amount-based brokers such as eToro may execute fractional units. Persist
    # the broker-reconciled USD notional explicitly so portfolio accounting does
    # not have to infer it from the integer RiskEngine quantity ceiling.
    notional_usd: float | None = None
    broker_position_id: str | None = None


class Broker(ABC):
    @abstractmethod
    def execute(self, proposal: TradeProposal) -> BrokerOrder:
        """Execute an already risk-approved trade proposal."""
        raise NotImplementedError
