from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trading_system.market_event import MarketEventKind


class WorkflowStepKey(str, Enum):
    TRACKING = "tracking"
    EVENT_IDENTIFIED = "event_identified"
    RELEASE = "release"
    ANALYSIS = "analysis"
    MARKET_REACTION = "market_reaction"
    STRATEGY = "strategy"
    RISK = "risk"
    PAPER = "paper"


class WorkflowStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    ACTION_REQUIRED = "action_required"


class WorkflowStepMode(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    SKIP = "skip"


@dataclass(frozen=True)
class WorkflowStepDefinition:
    key: WorkflowStepKey
    mode: WorkflowStepMode = WorkflowStepMode.REQUIRED

    def __post_init__(self) -> None:
        if not isinstance(self.key, WorkflowStepKey):
            raise ValueError("key must be a WorkflowStepKey")
        if not isinstance(self.mode, WorkflowStepMode):
            raise ValueError("mode must be a WorkflowStepMode")


@dataclass(frozen=True)
class EventWorkflowProfile:
    """Producer-neutral policy describing which stages apply to one event.

    The event profile is observation-only unless a canonical trading task exists.
    Strategy, Risk, and PAPER are appended only for a trading-task-backed profile,
    so tracking an event never implies that MarketAI should attempt a trade.
    Runtime state is represented separately by ``WorkflowStepStatus`` so partially
    completed and non-linear workflows can be reported truthfully.
    """

    profile_id: str
    steps: tuple[WorkflowStepDefinition, ...]

    def __post_init__(self) -> None:
        profile_id = self.profile_id.strip()
        if not profile_id:
            raise ValueError("profile_id must not be blank")
        if not self.steps:
            raise ValueError("steps must not be empty")

        keys = tuple(step.key for step in self.steps)
        if len(set(keys)) != len(keys):
            raise ValueError("workflow step keys must be unique")

        object.__setattr__(self, "profile_id", profile_id)

    def mode_for(self, key: WorkflowStepKey) -> WorkflowStepMode:
        for step in self.steps:
            if step.key is key:
                return step.mode
        raise KeyError(key.value)


@dataclass(frozen=True)
class WorkflowStepState:
    key: WorkflowStepKey
    status: WorkflowStepStatus


_COMMON_PREFIX = (
    WorkflowStepDefinition(WorkflowStepKey.TRACKING),
    WorkflowStepDefinition(WorkflowStepKey.EVENT_IDENTIFIED),
)
_OBSERVATION_SUFFIX = (
    WorkflowStepDefinition(WorkflowStepKey.ANALYSIS),
    WorkflowStepDefinition(WorkflowStepKey.MARKET_REACTION),
)
_TRADING_SUFFIX = (
    WorkflowStepDefinition(WorkflowStepKey.STRATEGY),
    WorkflowStepDefinition(WorkflowStepKey.RISK),
    WorkflowStepDefinition(WorkflowStepKey.PAPER),
)

EARNINGS_WORKFLOW = EventWorkflowProfile(
    profile_id="earnings_documented_observation_v1",
    steps=(
        *_COMMON_PREFIX,
        WorkflowStepDefinition(WorkflowStepKey.RELEASE, WorkflowStepMode.REQUIRED),
        *_OBSERVATION_SUFFIX,
    ),
)

EARNINGS_TRADING_WORKFLOW = EventWorkflowProfile(
    profile_id="earnings_documented_trading_v1",
    steps=(*EARNINGS_WORKFLOW.steps, *_TRADING_SUFFIX),
)

CONTENT_EVENT_WORKFLOW = EventWorkflowProfile(
    profile_id="content_event_observation_v1",
    steps=(
        *_COMMON_PREFIX,
        WorkflowStepDefinition(WorkflowStepKey.RELEASE, WorkflowStepMode.SKIP),
        *_OBSERVATION_SUFFIX,
    ),
)

CONTENT_EVENT_TRADING_WORKFLOW = EventWorkflowProfile(
    profile_id="content_event_trading_v1",
    steps=(*CONTENT_EVENT_WORKFLOW.steps, *_TRADING_SUFFIX),
)


_DOCUMENTED_RELEASE_KINDS = frozenset(
    {
        MarketEventKind.EARNINGS,
        MarketEventKind.GUIDANCE,
        MarketEventKind.TRADING_UPDATE,
        MarketEventKind.DIVIDEND,
    }
)


def workflow_profile_for_kind(
    kind: MarketEventKind,
    *,
    has_trading_task: bool = False,
) -> EventWorkflowProfile:
    """Select policy from event kind plus explicit canonical trading-task presence."""
    if not isinstance(kind, MarketEventKind):
        raise ValueError("kind must be a MarketEventKind")
    if not isinstance(has_trading_task, bool):
        raise ValueError("has_trading_task must be a bool")

    if kind in _DOCUMENTED_RELEASE_KINDS:
        return EARNINGS_TRADING_WORKFLOW if has_trading_task else EARNINGS_WORKFLOW
    return CONTENT_EVENT_TRADING_WORKFLOW if has_trading_task else CONTENT_EVENT_WORKFLOW


def initial_workflow_state(profile: EventWorkflowProfile) -> tuple[WorkflowStepState, ...]:
    """Return a neutral initial projection without inventing runtime completion."""
    if not isinstance(profile, EventWorkflowProfile):
        raise ValueError("profile must be an EventWorkflowProfile")
    return tuple(
        WorkflowStepState(
            key=step.key,
            status=(
                WorkflowStepStatus.SKIPPED
                if step.mode is WorkflowStepMode.SKIP
                else WorkflowStepStatus.PENDING
            ),
        )
        for step in profile.steps
    )
