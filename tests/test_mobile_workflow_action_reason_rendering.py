from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "mobile" / "src" / "components" / "TrackedEventsSection.tsx"


class MobileWorkflowActionReasonRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = COMPONENT.read_text(encoding="utf-8")

    def test_action_reason_is_rendered_only_for_action_required_step(self) -> None:
        self.assertIn(
            "step.status === 'action_required' && step.action_reason",
            self.source,
        )
        self.assertIn("{step.action_reason}", self.source)

    def test_action_reason_is_visually_bounded(self) -> None:
        self.assertIn("numberOfLines={3}", self.source)

    def test_renderer_does_not_map_action_code_or_add_navigation(self) -> None:
        workflow_source = self.source.split("function TrackedEventWorkflow", 1)[1].split(
            "const WORKFLOW_STEP_LABELS", 1
        )[0]
        self.assertNotIn("action_code", workflow_source)
        self.assertNotIn("router.", workflow_source)
        self.assertNotIn("onPress", workflow_source)


if __name__ == "__main__":
    unittest.main()
