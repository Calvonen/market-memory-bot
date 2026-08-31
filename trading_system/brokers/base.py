from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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


def broker_order_payload(order: BrokerOrder) -> dict[str, Any]:
    """Serialize one broker order identically for attempts and terminal runs."""
    payload: dict[str, Any] = {
        "order_id": order.order_id,
        "instrument": order.instrument,
        "direction": order.direction.value,
        "quantity": order.quantity,
        "reference_price": order.reference_price,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
    }
    if order.notional_usd is not None:
        payload["notional_usd"] = order.notional_usd
    if order.broker_position_id is not None:
        payload["broker_position_id"] = order.broker_position_id
    return payload


class Broker(ABC):
    @abstractmethod
    def execute(self, proposal: TradeProposal) -> BrokerOrder:
        """Execute an already risk-approved trade proposal."""
        raise NotImplementedError
