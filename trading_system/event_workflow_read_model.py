from __future__ import annotations

from dataclasses import dataclass

from trading_system.event_workflow import (
    EventWorkflowProfile,
    WorkflowStepDefinition,
    WorkflowStepState,
    workflow_profile_for_kind,
)
from trading_system.event_workflow_readiness import (
    WorkflowExecutionOutcome,
    WorkflowReadinessEvidence,
    project_workflow_readiness,
)
from trading_system.market_event import MarketEventKind
from trading_system.models import TradingMode
from trading_system.tracked_event_repository import PersistentTrackedEvent


@dataclass(frozen=True)
class WorkflowReadStep:
    key: str
    mode: str
    status: str


@dataclass(frozen=True)
class EventWorkflowReadModel:
    """API-ready canonical workflow projection for one tracked event.

    Trading mode is never inferred from tracking. Callers must pass the mode from
    an explicit canonical trading-task context, or ``None`` for observation-only
    tracking. This keeps the read model producer-neutral and prevents a tracked
    event from silently becoming a trading workflow.
    """

    event_id: str
    profile_id: str
    trading_mode: str | None
    steps: tuple[WorkflowReadStep, ...]


def build_event_workflow_read_model(
    event: PersistentTrackedEvent,
    evidence: WorkflowReadinessEvidence,
    *,
    trading_mode: TradingMode | None = None,
) -> EventWorkflowReadModel:
    if not isinstance(event, PersistentTrackedEvent):
        raise ValueError("event must be a PersistentTrackedEvent")
    if not isinstance(evidence, WorkflowReadinessEvidence):
        raise ValueError("evidence must be WorkflowReadinessEvidence")
    if trading_mode is not None and not isinstance(trading_mode, TradingMode):
        raise ValueError("trading_mode must be a TradingMode or None")

    try:
        kind = MarketEventKind(event.kind)
    except ValueError as exc:
        raise ValueError(f"unsupported tracked event kind: {event.kind}") from exc

    _require_compatible_trading_evidence(trading_mode, evidence)
    profile = workflow_profile_for_kind(kind, trading_mode=trading_mode)
    states = project_workflow_readiness(profile, evidence)
    return EventWorkflowReadModel(
        event_id=event.event_id,
        profile_id=profile.profile_id,
        trading_mode=trading_mode.value if trading_mode is not None else None,
        steps=_join_steps(profile, states),
    )


def _require_compatible_trading_evidence(
    trading_mode: TradingMode | None,
    evidence: WorkflowReadinessEvidence,
) -> None:
    """Fail closed when PAPER-backed evidence is applied to a LIVE workflow.

    The durable evidence loader currently obtains Strategy/Risk/execution state
    from ``get_event_paper_trade_state``. Until a LIVE-scoped evidence source is
    introduced, a LIVE projection may therefore expose the workflow shape but
    must not consume any persisted trading progress from this evidence object.
    """
    if trading_mode is not TradingMode.LIVE:
        return

    has_trading_progress = (
        evidence.strategy_present
        or evidence.risk_present
        or evidence.execution_outcome is not WorkflowExecutionOutcome.NOT_STARTED
    )
    if has_trading_progress:
        raise ValueError("LIVE workflow cannot consume PAPER trading evidence")


def _join_steps(
    profile: EventWorkflowProfile,
    states: tuple[WorkflowStepState, ...],
) -> tuple[WorkflowReadStep, ...]:
    if len(profile.steps) != len(states):
        raise RuntimeError("workflow profile and readiness projection differ in length")

    result: list[WorkflowReadStep] = []
    for definition, state in zip(profile.steps, states, strict=True):
        _require_same_step(definition, state)
        result.append(
            WorkflowReadStep(
                key=definition.key.value,
                mode=definition.mode.value,
                status=state.status.value,
            )
        )
    return tuple(result)


def _require_same_step(
    definition: WorkflowStepDefinition,
    state: WorkflowStepState,
) -> None:
    if definition.key is not state.key:
        raise RuntimeError("workflow profile and readiness projection keys differ")
