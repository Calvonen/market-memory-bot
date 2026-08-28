from __future__ import annotations

import unittest

from trading_system.event_workflow import (
    CONTENT_EVENT_WORKFLOW,
    EARNINGS_PAPER_WORKFLOW,
    EARNINGS_WORKFLOW,
    WorkflowStepKey,
    WorkflowStepStatus,
)
from trading_system.event_workflow_readiness import (
    WorkflowExecutionOutcome,
    WorkflowReadinessEvidence,
    project_workflow_readiness,
)
from trading_system.tracked_event_repository import TrackedEventStatus


class EventWorkflowReadinessTests(unittest.TestCase):
    def _by_key(self, states):
        return {state.key: state.status for state in states}

    def test_tracked_event_starts_pending_with_identity_completed(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_WORKFLOW,
            WorkflowReadinessEvidence(tracked_status=TrackedEventStatus.TRACKED),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.TRACKING], WorkflowStepStatus.PENDING)
        self.assertEqual(by_key[WorkflowStepKey.EVENT_IDENTIFIED], WorkflowStepStatus.COMPLETED)
        self.assertEqual(by_key[WorkflowStepKey.RELEASE], WorkflowStepStatus.PENDING)
        self.assertEqual(by_key[WorkflowStepKey.ANALYSIS], WorkflowStepStatus.PENDING)
        self.assertEqual(by_key[WorkflowStepKey.MARKET_REACTION], WorkflowStepStatus.PENDING)

    def test_monitoring_with_reaction_is_running_without_inventing_release_progress(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.MONITORING,
                reaction_present=True,
            ),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.TRACKING], WorkflowStepStatus.RUNNING)
        self.assertEqual(by_key[WorkflowStepKey.RELEASE], WorkflowStepStatus.PENDING)
        self.assertEqual(by_key[WorkflowStepKey.ANALYSIS], WorkflowStepStatus.PENDING)
        self.assertEqual(by_key[WorkflowStepKey.MARKET_REACTION], WorkflowStepStatus.RUNNING)

    def test_release_failure_becomes_action_required(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.MONITORING,
                release_failed=True,
                reaction_present=True,
            ),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.RELEASE], WorkflowStepStatus.ACTION_REQUIRED)
        self.assertEqual(by_key[WorkflowStepKey.MARKET_REACTION], WorkflowStepStatus.RUNNING)

    def test_content_event_keeps_release_skipped_even_when_release_failure_flag_is_set(self) -> None:
        states = project_workflow_readiness(
            CONTENT_EVENT_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.MONITORING,
                release_failed=True,
            ),
        )
        self.assertEqual(
            self._by_key(states)[WorkflowStepKey.RELEASE],
            WorkflowStepStatus.SKIPPED,
        )

    def test_completed_release_analysis_and_reaction_can_be_projected_independently(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.COMPLETED,
                release_document_present=True,
                analysis_present=True,
                reaction_present=True,
            ),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.TRACKING], WorkflowStepStatus.COMPLETED)
        self.assertEqual(by_key[WorkflowStepKey.RELEASE], WorkflowStepStatus.COMPLETED)
        self.assertEqual(by_key[WorkflowStepKey.ANALYSIS], WorkflowStepStatus.COMPLETED)
        self.assertEqual(by_key[WorkflowStepKey.MARKET_REACTION], WorkflowStepStatus.COMPLETED)

    def test_completed_tracking_without_reaction_fails_closed(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_WORKFLOW,
            WorkflowReadinessEvidence(tracked_status=TrackedEventStatus.COMPLETED),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.TRACKING], WorkflowStepStatus.COMPLETED)
        self.assertEqual(by_key[WorkflowStepKey.MARKET_REACTION], WorkflowStepStatus.FAILED)

    def test_paper_trading_stages_follow_only_persisted_evidence(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_PAPER_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.COMPLETED,
                release_document_present=True,
                analysis_present=True,
                reaction_present=True,
                strategy_present=True,
                risk_present=False,
            ),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.STRATEGY], WorkflowStepStatus.COMPLETED)
        self.assertEqual(by_key[WorkflowStepKey.RISK], WorkflowStepStatus.PENDING)
        self.assertEqual(by_key[WorkflowStepKey.PAPER], WorkflowStepStatus.PENDING)

    def test_execution_outcomes_distinguish_accepted_filled_and_terminal_no_trade(self) -> None:
        expected = {
            WorkflowExecutionOutcome.ACCEPTED: WorkflowStepStatus.RUNNING,
            WorkflowExecutionOutcome.FILLED: WorkflowStepStatus.COMPLETED,
            WorkflowExecutionOutcome.NO_TRADE: WorkflowStepStatus.SKIPPED,
            WorkflowExecutionOutcome.REJECTED: WorkflowStepStatus.SKIPPED,
            WorkflowExecutionOutcome.FAILED: WorkflowStepStatus.FAILED,
        }
        for outcome, status in expected.items():
            with self.subTest(outcome=outcome):
                states = project_workflow_readiness(
                    EARNINGS_PAPER_WORKFLOW,
                    WorkflowReadinessEvidence(
                        tracked_status=TrackedEventStatus.COMPLETED,
                        strategy_present=True,
                        risk_present=True,
                        execution_outcome=outcome,
                    ),
                )
                self.assertEqual(self._by_key(states)[WorkflowStepKey.PAPER], status)

    def test_terminal_no_trade_skips_unrun_strategy_and_risk(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_PAPER_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.COMPLETED,
                execution_outcome=WorkflowExecutionOutcome.NO_TRADE,
            ),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.STRATEGY], WorkflowStepStatus.SKIPPED)
        self.assertEqual(by_key[WorkflowStepKey.RISK], WorkflowStepStatus.SKIPPED)
        self.assertEqual(by_key[WorkflowStepKey.PAPER], WorkflowStepStatus.SKIPPED)

    def test_terminal_execution_preserves_completed_upstream_trading_stages(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_PAPER_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.COMPLETED,
                strategy_present=True,
                risk_present=False,
                execution_outcome=WorkflowExecutionOutcome.REJECTED,
            ),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.STRATEGY], WorkflowStepStatus.COMPLETED)
        self.assertEqual(by_key[WorkflowStepKey.RISK], WorkflowStepStatus.SKIPPED)
        self.assertEqual(by_key[WorkflowStepKey.PAPER], WorkflowStepStatus.SKIPPED)

    def test_failed_tracking_does_not_erase_completed_release_or_analysis(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.FAILED,
                release_document_present=True,
                analysis_present=True,
            ),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.TRACKING], WorkflowStepStatus.FAILED)
        self.assertEqual(by_key[WorkflowStepKey.RELEASE], WorkflowStepStatus.COMPLETED)
        self.assertEqual(by_key[WorkflowStepKey.ANALYSIS], WorkflowStepStatus.COMPLETED)
        self.assertEqual(by_key[WorkflowStepKey.MARKET_REACTION], WorkflowStepStatus.FAILED)

    def test_failed_tracking_overrides_partial_reaction_presence(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.FAILED,
                reaction_present=True,
            ),
        )
        self.assertEqual(
            self._by_key(states)[WorkflowStepKey.MARKET_REACTION],
            WorkflowStepStatus.FAILED,
        )

    def test_cancelled_tracking_skips_partial_reaction_presence(self) -> None:
        states = project_workflow_readiness(
            EARNINGS_WORKFLOW,
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.CANCELLED,
                reaction_present=True,
            ),
        )
        by_key = self._by_key(states)
        self.assertEqual(by_key[WorkflowStepKey.TRACKING], WorkflowStepStatus.SKIPPED)
        self.assertEqual(by_key[WorkflowStepKey.MARKET_REACTION], WorkflowStepStatus.SKIPPED)

    def test_evidence_rejects_conflicting_release_terminal_facts(self) -> None:
        with self.assertRaisesRegex(ValueError, "both present and failed"):
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.MONITORING,
                release_document_present=True,
                release_failed=True,
            )

    def test_evidence_rejects_non_boolean_facts(self) -> None:
        with self.assertRaisesRegex(ValueError, "analysis_present"):
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.MONITORING,
                analysis_present=1,  # type: ignore[arg-type]
            )

    def test_evidence_rejects_non_execution_outcome(self) -> None:
        with self.assertRaisesRegex(ValueError, "execution_outcome"):
            WorkflowReadinessEvidence(
                tracked_status=TrackedEventStatus.MONITORING,
                execution_outcome="filled",  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
