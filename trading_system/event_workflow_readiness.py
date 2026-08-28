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
    """

    tracked_status: TrackedEventStatus
    release_document_present: bool = False
    release_failed: bool = False
    analysis_present: bool = False
    reaction_present: bool = False
    strategy_present: bool = False
    risk_present: bool = False
    execution_outcome: WorkflowExecutionOutcome = WorkflowExecutionOutcome.NOT_STARTED

    def __post_init__(self) -> None:
        if not isinstance(self.tracked_status, TrackedEventStatus):
            raise ValueError("tracked_status must be a TrackedEventStatus")
        for field_name in (
            "release_document_present",
            "release_failed",
            "analysis_present",
            "reaction_present",
            "strategy_present",
            "risk_present",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a bool")
        if not isinstance(self.execution_outcome, WorkflowExecutionOutcome):
            raise ValueError("execution_outcome must be a WorkflowExecutionOutcome")
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
        else:  # fail closed if the workflow vocabulary grows without projection logic
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
    if evidence.release_failed:
        return WorkflowStepStatus.ACTION_REQUIRED
    return WorkflowStepStatus.PENDING


def _reaction_status(evidence: WorkflowReadinessEvidence) -> WorkflowStepStatus:
    # Terminal tracked states win over partial reaction presence: the worker will
    # not resume a failed/cancelled runtime, so it must never remain RUNNING.
    if evidence.tracked_status is TrackedEventStatus.FAILED:
        return WorkflowStepStatus.FAILED
    if evidence.tracked_status is TrackedEventStatus.CANCELLED:
        return WorkflowStepStatus.SKIPPED
    if evidence.reaction_present:
        if evidence.tracked_status is TrackedEventStatus.COMPLETED:
            return WorkflowStepStatus.COMPLETED
        return WorkflowStepStatus.RUNNING
    # COMPLETED is also terminal. If no reaction row was ever persisted, the
    # reaction stage cannot advance on a later poll, so fail closed instead of
    # projecting an impossible perpetual PENDING state.
    if evidence.tracked_status is TrackedEventStatus.COMPLETED:
        return WorkflowStepStatus.FAILED
    return WorkflowStepStatus.PENDING


def _trading_stage_status(
    present: bool,
    evidence: WorkflowReadinessEvidence,
) -> WorkflowStepStatus:
    if present:
        return WorkflowStepStatus.COMPLETED
    # Terminal execution outcomes close the trading workflow. If a strategy or
    # risk stage was never reached (for example expired_no_trade before the
    # pipeline ran), it can no longer advance and must not remain PENDING.
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
