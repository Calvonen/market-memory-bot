from __future__ import annotations

from uuid import uuid4

from trading_system.brokers.base import Broker, BrokerOrder
from trading_system.models import Direction, RiskStatus, TradeProposal, TradingMode


class PaperBroker(Broker):
    """In-memory paper broker.

    It deliberately refuses LIVE proposals and any proposal that has not passed
    the deterministic RiskEngine.
    """

    def __init__(self) -> None:
        self.orders: list[BrokerOrder] = []

    def execute(self, proposal: TradeProposal) -> BrokerOrder:
        if proposal.mode is not TradingMode.PAPER:
            raise ValueError("PaperBroker only accepts PAPER proposals")
        if proposal.risk.status is not RiskStatus.PASS:
            raise ValueError("RiskEngine did not approve this proposal")
        if proposal.candidate.direction not in {Direction.LONG, Direction.SHORT}:
            raise ValueError("Broker cannot execute NO_TRADE")
        if proposal.candidate.entry is None or proposal.candidate.entry <= 0:
            raise ValueError("Proposal has no valid entry price")
        if proposal.risk.max_quantity < 1:
            raise ValueError("Risk-approved quantity must be at least one")

        order = BrokerOrder(
            order_id=f"paper-{uuid4().hex}",
            instrument=proposal.candidate.instrument,
            direction=proposal.candidate.direction,
            quantity=proposal.risk.max_quantity,
            reference_price=proposal.candidate.entry,
            status="FILLED_SIMULATED",
        )
        self.orders.append(order)
        return order
