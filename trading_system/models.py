from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from uuid import uuid4


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


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid4().hex


@dataclass(frozen=True)
class ScoreBreakdown:
    fundamental: int = 0
    catalyst: int = 0
    technical: int = 0
    market_memory: int = 0
    news_sentiment: int = 0

    @property
    def total(self) -> int:
        return self.fundamental + self.catalyst + self.technical + self.market_memory + self.news_sentiment


@dataclass(frozen=True)
class ComponentAssessment:
    name: str
    direction: Direction
    score: int
    max_score: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyInputs:
    instrument: str
    fundamental: ComponentAssessment
    catalyst: ComponentAssessment
    technical: ComponentAssessment
    market_memory: ComponentAssessment
    news_sentiment: ComponentAssessment
    source_event_id: str | None = None
    invalidation: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyDecision:
    instrument: str
    direction: Direction
    confidence: int
    scores: ScoreBreakdown
    rationale: tuple[str, ...] = ()
    invalidation: tuple[str, ...] = ()
    source_event_id: str | None = None
    long_evidence: int = 0
    short_evidence: int = 0
    decision_id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class TradeLevels:
    entry: float | None
    stop: float | None
    target_1: float | None
    target_2: float | None = None


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
    strategy_decision_id: str | None = None
    candidate_id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


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
    decision_id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class TradeProposal:
    candidate: TradeCandidate
    risk: RiskDecision
    mode: TradingMode = TradingMode.PAPER
    strategy_decision: StrategyDecision | None = None
    proposal_id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class EventExpectation:
    event_id: str
    instrument: str
    event_name: str
    scheduled_date: date
    consensus: dict[str, float | str | None] = field(default_factory=dict)
    important_kpis: tuple[str, ...] = ()
    bull_case: tuple[str, ...] = ()
    base_case: tuple[str, ...] = ()
    bear_case: tuple[str, ...] = ()
    triggers: dict[str, float | str] = field(default_factory=dict)
    invalidation_conditions: tuple[str, ...] = ()
    source_name: str | None = None
    source_url: str | None = None
    source_as_of: date | None = None
    version: int = 1
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class EventActuals:
    event_id: str
    instrument: str
    published_at: datetime
    source_url: str
    metrics: dict[str, float | str | None] = field(default_factory=dict)
    guidance_summary: str | None = None
    management_summary: str | None = None
    price_reaction_pct: float | None = None
    source_document_id: str | None = None
    captured_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class EventMetricComparison:
    metric: str
    consensus: float | str | None
    actual: float | str | None
    absolute_delta: float | None = None
    percent_delta: float | None = None


@dataclass(frozen=True)
class EventComparison:
    event_id: str
    instrument: str
    metrics: tuple[EventMetricComparison, ...]
    missing_important_kpis: tuple[str, ...] = ()
    guidance_summary: str | None = None
    price_reaction_pct: float | None = None
