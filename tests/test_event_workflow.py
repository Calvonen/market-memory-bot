from __future__ import annotations

import unittest

from trading_system.event_workflow import (
    CONTENT_EVENT_WORKFLOW,
    EARNINGS_WORKFLOW,
    EventWorkflowProfile,
    WorkflowStepDefinition,
    WorkflowStepKey,
    WorkflowStepMode,
    WorkflowStepStatus,
    initial_workflow_state,
    workflow_profile_for_kind,
)
from trading_system.market_event import MarketEventKind


class EventWorkflowProfileTests(unittest.TestCase):
    def test_earnings_requires_release_stage(self) -> None:
        profile = workflow_profile_for_kind(MarketEventKind.EARNINGS)
        self.assertIs(profile, EARNINGS_WORKFLOW)
        self.assertEqual(profile.mode_for(WorkflowStepKey.RELEASE), WorkflowStepMode.REQUIRED)

    def test_news_keeps_common_path_but_skips_release_stage(self) -> None:
        profile = workflow_profile_for_kind(MarketEventKind.NEWS)
        self.assertIs(profile, CONTENT_EVENT_WORKFLOW)
        self.assertEqual(profile.mode_for(WorkflowStepKey.RELEASE), WorkflowStepMode.SKIP)
        self.assertEqual(
            tuple(step.key for step in profile.steps),
            (
                WorkflowStepKey.TRACKING,
                WorkflowStepKey.EVENT_IDENTIFIED,
                WorkflowStepKey.RELEASE,
                WorkflowStepKey.ANALYSIS,
                WorkflowStepKey.MARKET_REACTION,
                WorkflowStepKey.STRATEGY,
                WorkflowStepKey.RISK,
                WorkflowStepKey.PAPER,
            ),
        )

    def test_non_document_release_kinds_do_not_inherit_earnings_requirement(self) -> None:
        for kind in (
            MarketEventKind.ACQUISITION,
            MarketEventKind.MANAGEMENT_CHANGE,
            MarketEventKind.CUSTOM,
        ):
            with self.subTest(kind=kind):
                self.assertIs(workflow_profile_for_kind(kind), CONTENT_EVENT_WORKFLOW)

    def test_guidance_and_trading_update_use_documented_release_profile(self) -> None:
        for kind in (
            MarketEventKind.GUIDANCE,
            MarketEventKind.TRADING_UPDATE,
            MarketEventKind.DIVIDEND,
        ):
            with self.subTest(kind=kind):
                self.assertIs(workflow_profile_for_kind(kind), EARNINGS_WORKFLOW)

    def test_initial_state_marks_only_profile_skips_as_skipped(self) -> None:
        states = initial_workflow_state(CONTENT_EVENT_WORKFLOW)
        by_key = {state.key: state.status for state in states}
        self.assertEqual(by_key[WorkflowStepKey.RELEASE], WorkflowStepStatus.SKIPPED)
        self.assertTrue(
            all(
                status is WorkflowStepStatus.PENDING
                for key, status in by_key.items()
                if key is not WorkflowStepKey.RELEASE
            )
        )

    def test_runtime_status_contract_supports_partial_and_action_required_states(self) -> None:
        self.assertEqual(
            {status.value for status in WorkflowStepStatus},
            {
                "pending",
                "running",
                "completed",
                "skipped",
                "failed",
                "action_required",
            },
        )

    def test_profile_rejects_duplicate_step_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            EventWorkflowProfile(
                profile_id="bad",
                steps=(
                    WorkflowStepDefinition(WorkflowStepKey.TRACKING),
                    WorkflowStepDefinition(WorkflowStepKey.TRACKING),
                ),
            )

    def test_profile_rejects_invalid_kind_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "MarketEventKind"):
            workflow_profile_for_kind("earnings")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
