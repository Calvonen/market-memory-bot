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
    notional_usd: float | None = None
    broker_position_id: str | None = None


def broker_order_payload(order: BrokerOrder) -> dict[str, Any]:
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


def broker_order_from_payload(payload: dict[str, Any]) -> BrokerOrder:
    try:
        created_at = datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00"))
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        raw_notional = payload.get("notional_usd")
        raw_position_id = payload.get("broker_position_id")
        return BrokerOrder(
            order_id=str(payload["order_id"]),
            instrument=str(payload["instrument"]),
            direction=Direction(str(payload["direction"])),
            quantity=int(payload["quantity"]),
            reference_price=float(payload["reference_price"]),
            status=str(payload["status"]),
            created_at=created_at.astimezone(UTC),
            notional_usd=float(raw_notional) if raw_notional is not None else None,
            broker_position_id=str(raw_position_id) if raw_position_id not in (None, "") else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("persisted broker attempt contains malformed order payload") from exc


class Broker(ABC):
    # Extended-hours order submission must be proven against the broker API
    # contract and opted in explicitly by a concrete broker implementation.
    # Market/session observability alone must never imply order capability.
    supports_extended_hours_orders = False

    @abstractmethod
    def execute(self, proposal: TradeProposal) -> BrokerOrder:
        raise NotImplementedError
