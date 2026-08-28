from __future__ import annotations

from dataclasses import dataclass

from trading_system.event_workflow import (
    EventWorkflowProfile,
    WorkflowStepKey,
    WorkflowStepMode,
    WorkflowStepState,
    WorkflowStepStatus,
)
from trading_system.tracked_event_repository import TrackedEventStatus


@dataclass(frozen=True)
class WorkflowReadinessEvidence:
    """Canonical persisted evidence used to project workflow readiness.

    This object is intentionally producer-neutral.  Callers populate it from the
    tracked-event, release, reaction, and trading-task persistence layers.  The
    projector never invents progress that is not present in durable state.
    """

    tracked_status: TrackedEventStatus
    release_document_present: bool = False
    release_failed: bool = False
    analysis_present: bool = False
    reaction_present: bool = False
    strategy_present: bool = False
    risk_present: bool = False
    execution_present: bool = False

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
            "execution_present",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a bool")
        if self.release_document_present and self.release_failed:
            raise ValueError("release cannot be both present and failed")


def project_workflow_readiness(
    profile: EventWorkflowProfile,
    evidence: WorkflowReadinessEvidence,
) -> tuple[WorkflowStepState, ...]:
    """Project canonical workflow state from durable evidence.

    The projection is intentionally non-linear: release/analysis/reaction can be
    complete independently when persisted evidence proves that they are.  A
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
            status = (
                WorkflowStepStatus.COMPLETED
                if evidence.strategy_present
                else WorkflowStepStatus.PENDING
            )
        elif step.key is WorkflowStepKey.RISK:
            status = (
                WorkflowStepStatus.COMPLETED
                if evidence.risk_present
                else WorkflowStepStatus.PENDING
            )
        elif step.key in (WorkflowStepKey.PAPER, WorkflowStepKey.LIVE):
            status = (
                WorkflowStepStatus.COMPLETED
                if evidence.execution_present
                else WorkflowStepStatus.PENDING
            )
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
    if evidence.reaction_present:
        if evidence.tracked_status is TrackedEventStatus.COMPLETED:
            return WorkflowStepStatus.COMPLETED
        return WorkflowStepStatus.RUNNING
    if evidence.tracked_status is TrackedEventStatus.FAILED:
        return WorkflowStepStatus.FAILED
    if evidence.tracked_status is TrackedEventStatus.CANCELLED:
        return WorkflowStepStatus.SKIPPED
    return WorkflowStepStatus.PENDING
