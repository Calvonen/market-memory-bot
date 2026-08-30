from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trading_system.event_workflow import (
    EventWorkflowProfile,
    WorkflowStepKey,
    WorkflowStepMode,
    WorkflowStepState,
    WorkflowStepStatus,
)
from trading_system.models import TradingMode
from trading_system.tracked_event_repository import TrackedEventStatus


class WorkflowExecutionOutcome(str, Enum):
    """Normalized durable outcome for the execution stage.

    Repository adapters translate broker/paper persistence into this vocabulary;
    the readiness projector therefore never treats mere order presence as proof
    that execution completed.
    """

    NOT_STARTED = "not_started"
    ACCEPTED = "accepted"
    FILLED = "filled"
    NO_TRADE = "no_trade"
    REJECTED = "rejected"
    FAILED = "failed"


_TERMINAL_EXECUTION_OUTCOMES = frozenset(
    {
        WorkflowExecutionOutcome.NO_TRADE,
        WorkflowExecutionOutcome.REJECTED,
        WorkflowExecutionOutcome.FAILED,
    }
)


@dataclass(frozen=True)
class WorkflowReadinessEvidence:
    """Canonical persisted evidence used to project workflow readiness.

    This object is intentionally producer-neutral. Callers populate it from the
    tracked-event, release, reaction, and trading-task persistence layers. The
    projector never invents progress that is not present in durable state.

    ``event_id`` binds a loaded evidence snapshot to its canonical tracked event.
    ``trading_mode`` records provenance for Strategy/Risk/execution evidence. It
    is independent from the workflow profile selected by the caller, so PAPER
    persistence can never be mistaken for LIVE broker evidence.
    """

    tracked_status: TrackedEventStatus
    event_id: str | None = None
    release_document_present: bool = False
    release_skipped: bool = False
    release_failed: bool = False
    release_action_code: str | None = None
    release_action_reason: str | None = None
    analysis_present: bool = False
    reaction_present: bool = False
    strategy_present: bool = False
    risk_present: bool = False
    execution_outcome: WorkflowExecutionOutcome = WorkflowExecutionOutcome.NOT_STARTED
    trading_mode: TradingMode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tracked_status, TrackedEventStatus):
            raise ValueError("tracked_status must be a TrackedEventStatus")
        if self.event_id is not None:
            if not isinstance(self.event_id, str) or not self.event_id.strip():
                raise ValueError("event_id must be a nonblank string or None")
        for field_name in (
            "release_document_present",
            "release_skipped",
            "release_failed",
            "analysis_present",
            "reaction_present",
            "strategy_present",
            "risk_present",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a bool")
        for field_name in ("release_action_code", "release_action_reason"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be a nonblank string or None")
        if (self.release_action_code is None) != (self.release_action_reason is None):
            raise ValueError("release action code and reason must be provided together")
        if self.release_action_code is not None and not self.release_failed:
            raise ValueError("release action metadata requires release_failed")
        if self.release_skipped and self.release_failed:
            raise ValueError("release cannot be both skipped and failed")
        if not isinstance(self.execution_outcome, WorkflowExecutionOutcome):
            raise ValueError("execution_outcome must be a WorkflowExecutionOutcome")
        if self.trading_mode is not None and not isinstance(self.trading_mode, TradingMode):
            raise ValueError("trading_mode must be a TradingMode or None")
        if self.release_document_present and self.release_failed:
            raise ValueError("release cannot be both present and failed")


def project_workflow_readiness(
    profile: EventWorkflowProfile,
    evidence: WorkflowReadinessEvidence,
) -> tuple[WorkflowStepState, ...]:
    """Project canonical workflow state from durable evidence.

    The projection is intentionally non-linear: release/analysis/reaction can be
    complete independently when persisted evidence proves that they are. A
    failed tracked-event runtime fails only the still-unfinished tracking and
    reaction stages; already completed evidence remains completed.
    """
    if not isinstance(profile, EventWorkflowProfile):
        raise ValueError("profile must be an EventWorkflowProfile")
    if not isinstance(evidence, WorkflowReadinessEvidence):
        raise ValueError("evidence must be WorkflowReadinessEvidence")

    states: list[WorkflowStepState] = []
    for step in profile.steps:
        if step.mode is WorkflowStepMode.SKIP:
            status = WorkflowStepStatus.SKIPPED
        elif step.key is WorkflowStepKey.TRACKING:
            status = _tracking_status(evidence)
        elif step.key is WorkflowStepKey.EVENT_IDENTIFIED:
            status = WorkflowStepStatus.COMPLETED
        elif step.key is WorkflowStepKey.RELEASE:
            status = _release_status(evidence)
        elif step.key is WorkflowStepKey.ANALYSIS:
            status = (
                WorkflowStepStatus.COMPLETED
                if evidence.analysis_present
                else WorkflowStepStatus.PENDING
            )
        elif step.key is WorkflowStepKey.MARKET_REACTION:
            status = _reaction_status(evidence)
        elif step.key is WorkflowStepKey.STRATEGY:
            status = _trading_stage_status(evidence.strategy_present, evidence)
        elif step.key is WorkflowStepKey.RISK:
            status = _trading_stage_status(evidence.risk_present, evidence)
        elif step.key in (WorkflowStepKey.PAPER, WorkflowStepKey.LIVE):
            status = _execution_status(evidence.execution_outcome)
        else:
            raise ValueError(f"unsupported workflow step: {step.key.value}")

        states.append(WorkflowStepState(key=step.key, status=status))
    return tuple(states)


def _tracking_status(evidence: WorkflowReadinessEvidence) -> WorkflowStepStatus:
    if evidence.tracked_status is TrackedEventStatus.FAILED:
        return WorkflowStepStatus.FAILED
    if evidence.tracked_status is TrackedEventStatus.CANCELLED:
        return WorkflowStepStatus.SKIPPED
    if evidence.tracked_status is TrackedEventStatus.COMPLETED:
        return WorkflowStepStatus.COMPLETED
    if evidence.tracked_status is TrackedEventStatus.MONITORING:
        return WorkflowStepStatus.RUNNING
    return WorkflowStepStatus.PENDING


def _release_status(evidence: WorkflowReadinessEvidence) -> WorkflowStepStatus:
    if evidence.release_document_present:
        return WorkflowStepStatus.COMPLETED
    if evidence.release_skipped:
        return WorkflowStepStatus.SKIPPED
    if evidence.release_failed:
        return WorkflowStepStatus.ACTION_REQUIRED
    return WorkflowStepStatus.PENDING


def _reaction_status(evidence: WorkflowReadinessEvidence) -> WorkflowStepStatus:
    if evidence.tracked_status is TrackedEventStatus.FAILED:
        return WorkflowStepStatus.FAILED
    if evidence.tracked_status is TrackedEventStatus.CANCELLED:
        return WorkflowStepStatus.SKIPPED
    if evidence.reaction_present:
        if evidence.tracked_status is TrackedEventStatus.COMPLETED:
            return WorkflowStepStatus.COMPLETED
        return WorkflowStepStatus.RUNNING
    if evidence.tracked_status is TrackedEventStatus.COMPLETED:
        return WorkflowStepStatus.FAILED
    return WorkflowStepStatus.PENDING


def _trading_stage_status(
    present: bool,
    evidence: WorkflowReadinessEvidence,
) -> WorkflowStepStatus:
    if present:
        return WorkflowStepStatus.COMPLETED
    if evidence.execution_outcome in _TERMINAL_EXECUTION_OUTCOMES:
        return WorkflowStepStatus.SKIPPED
    return WorkflowStepStatus.PENDING


def _execution_status(outcome: WorkflowExecutionOutcome) -> WorkflowStepStatus:
    if outcome is WorkflowExecutionOutcome.NOT_STARTED:
        return WorkflowStepStatus.PENDING
    if outcome is WorkflowExecutionOutcome.ACCEPTED:
        return WorkflowStepStatus.RUNNING
    if outcome is WorkflowExecutionOutcome.FILLED:
        return WorkflowStepStatus.COMPLETED
    if outcome in (WorkflowExecutionOutcome.NO_TRADE, WorkflowExecutionOutcome.REJECTED):
        return WorkflowStepStatus.SKIPPED
    if outcome is WorkflowExecutionOutcome.FAILED:
        return WorkflowStepStatus.FAILED
    raise ValueError(f"unsupported execution outcome: {outcome.value}")
