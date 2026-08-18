from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class TradingMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class RiskStatus(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"


@dataclass(frozen=True)
class TradeCandidate:
    instrument: str
    direction: Direction
    confidence: int
    entry: float | None
    stop: float | None
    target_1: float | None
    target_2: float | None = None
    rationale: tuple[str, ...] = ()
    source_event_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    cash: float
    open_positions: int
    instrument_exposure_pct: float = 0.0
    daily_pnl: float = 0.0
    spread_pct: float | None = None
    volatility_pct: float | None = None
    last_loss_at: datetime | None = None


@dataclass(frozen=True)
class RiskDecision:
    status: RiskStatus
    reasons: tuple[str, ...]
    max_risk_amount: float = 0.0
    max_position_value: float = 0.0
    max_quantity: int = 0
    reward_risk: float | None = None


@dataclass(frozen=True)
class TradeProposal:
    candidate: TradeCandidate
    risk: RiskDecision
    mode: TradingMode = TradingMode.PAPER


@dataclass(frozen=True)
class EventExpectation:
    event_id: str
    instrument: str
    event_name: str
    scheduled_at: datetime
    consensus: dict[str, float | str | None] = field(default_factory=dict)
    important_kpis: tuple[str, ...] = ()
    bull_case: tuple[str, ...] = ()
    base_case: tuple[str, ...] = ()
    bear_case: tuple[str, ...] = ()
    triggers: dict[str, float | str] = field(default_factory=dict)
    invalidation_conditions: tuple[str, ...] = ()
