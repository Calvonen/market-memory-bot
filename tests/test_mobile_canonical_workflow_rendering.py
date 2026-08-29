from pathlib import Path
import unittest


TRACKED_EVENTS_SERVICE = Path("mobile/src/services/tracked-events.ts")
TRACKED_EVENTS_COMPONENT = Path("mobile/src/components/TrackedEventsSection.tsx")


class MobileCanonicalWorkflowRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service_source = TRACKED_EVENTS_SERVICE.read_text(encoding="utf-8")
        cls.component_source = TRACKED_EVENTS_COMPONENT.read_text(encoding="utf-8")

    def test_service_uses_canonical_workflow_endpoint(self) -> None:
        self.assertIn("export function getTrackedEventWorkflow", self.service_source)
        self.assertIn("/api/v1/tracked-events/${encodeURIComponent(eventId)}/workflow", self.service_source)

    def test_service_preserves_nullable_canonical_trading_mode(self) -> None:
        self.assertIn("trading_mode: 'PAPER' | 'LIVE' | null;", self.service_source)

    def test_service_types_all_canonical_step_statuses(self) -> None:
        for status in (
            "'pending'",
            "'running'",
            "'completed'",
            "'skipped'",
            "'failed'",
            "'action_required'",
        ):
            self.assertIn(status, self.service_source)

    def test_component_renders_backend_step_order_without_sorting(self) -> None:
        workflow_start = self.component_source.index("function TrackedEventWorkflow(")
        workflow_end = self.component_source.index("const WORKFLOW_STEP_LABELS", workflow_start)
        workflow_source = self.component_source[workflow_start:workflow_end]
        self.assertIn("state.workflow.steps.map((step)", workflow_source)
        self.assertNotIn(".sort(", workflow_source)

    def test_component_does_not_infer_workflow_from_runtime_or_reaction(self) -> None:
        workflow_start = self.component_source.index("function TrackedEventWorkflow(")
        workflow_end = self.component_source.index("function TrackedEventResult", workflow_start)
        workflow_source = self.component_source[workflow_start:workflow_end]
        self.assertNotIn("reaction_anchor_at", workflow_source)
        self.assertNotIn("reference_price", workflow_source)
        self.assertNotIn("last_error", workflow_source)
        normalized_workflow_source = workflow_source.lower()
        self.assertNotIn(
            "paper",
            normalized_workflow_source.split("const workflow_step_labels", 1)[0],
        )

    def test_workflow_fetch_is_independent_from_reaction_fetch(self) -> None:
        details_start = self.component_source.index("export function TrackedEventDetails(")
        details_end = self.component_source.index("function TrackedEventWorkflow(", details_start)
        details_source = self.component_source[details_start:details_end]
        self.assertIn("void getTrackedEventLatestReaction(event.event_id)", details_source)
        self.assertIn("void getTrackedEventWorkflow(event.event_id)", details_source)
        self.assertNotIn("Promise.all", details_source)

    def test_action_required_and_completed_are_direct_status_labels(self) -> None:
        self.assertIn("action_required: 'Toimia tarvitaan'", self.component_source)
        self.assertIn("completed: 'Valmis'", self.component_source)
        self.assertIn("return WORKFLOW_STATUS_LABELS[step.status];", self.component_source)


if __name__ == "__main__":
    unittest.main()
