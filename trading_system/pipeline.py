from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from trading_system.brokers.base import BrokerOrder
from trading_system.brokers.paper import PaperBroker
from trading_system.models import (
    Direction,
    PortfolioState,
    StrategyDecision,
    StrategyInputs,
    TradeCandidate,
    TradeLevels,
    TradeProposal,
    TradingMode,
)
from trading_system.risk import RiskEngine
from trading_system.strategy import StrategyEngine


class DecisionJournal(Protocol):
    def record_strategy(self, decision: StrategyDecision) -> None: ...
    def record_proposal(self, proposal: TradeProposal) -> None: ...
    def record_order(self, order: BrokerOrder) -> None: ...


@dataclass
class InMemoryDecisionJournal:
    strategies: list[StrategyDecision] = field(default_factory=list)
    proposals: list[TradeProposal] = field(default_factory=list)
    orders: list[BrokerOrder] = field(default_factory=list)

    def record_strategy(self, decision: StrategyDecision) -> None:
        self.strategies.append(decision)

    def record_proposal(self, proposal: TradeProposal) -> None:
        self.proposals.append(proposal)

    def record_order(self, order: BrokerOrder) -> None:
        self.orders.append(order)


@dataclass(frozen=True)
class PipelineResult:
    strategy: StrategyDecision
    proposal: TradeProposal
    order: BrokerOrder | None


class PaperTradingPipeline:
    """Strategy -> candidate -> risk -> paper broker, with an audit journal at every step."""

    def __init__(
        self,
        *,
        strategy_engine: StrategyEngine | None = None,
        risk_engine: RiskEngine | None = None,
        broker: PaperBroker | None = None,
        journal: DecisionJournal | None = None,
    ) -> None:
        self.strategy_engine = strategy_engine or StrategyEngine()
        self.risk_engine = risk_engine or RiskEngine()
        self.broker = broker or PaperBroker()
        self.journal = journal or InMemoryDecisionJournal()

    def run(
        self,
        inputs: StrategyInputs,
        levels: TradeLevels,
        portfolio: PortfolioState,
    ) -> PipelineResult:
        strategy = self.strategy_engine.evaluate(inputs)
        self.journal.record_strategy(strategy)

        candidate = TradeCandidate(
            instrument=strategy.instrument,
            direction=strategy.direction,
            confidence=strategy.confidence,
            entry=levels.entry,
            stop=levels.stop,
            target_1=levels.target_1,
            target_2=levels.target_2,
            rationale=strategy.rationale,
            source_event_id=strategy.source_event_id,
            strategy_decision_id=strategy.decision_id,
        )

        proposal = self.risk_engine.evaluate(
            candidate,
            portfolio,
            requested_mode=TradingMode.PAPER,
        )
        self.journal.record_proposal(proposal)

        order: BrokerOrder | None = None
        if strategy.direction in {Direction.LONG, Direction.SHORT} and proposal.risk.status.value == "PASS":
            order = self.broker.execute(proposal)
            self.journal.record_order(order)

        return PipelineResult(strategy=strategy, proposal=proposal, order=order)
