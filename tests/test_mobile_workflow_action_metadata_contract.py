from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "mobile" / "src" / "services" / "tracked-events.ts"


class MobileWorkflowActionMetadataContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SERVICE.read_text(encoding="utf-8")

    def test_workflow_step_preserves_backend_owned_action_metadata(self):
        self.assertIn("action_target: string | null;", self.source)
        self.assertIn("action_code: string | null;", self.source)
        self.assertIn("action_reason: string | null;", self.source)

    def test_workflow_client_still_reads_canonical_endpoint(self):
        self.assertIn(
            "`/api/v1/tracked-events/${encodeURIComponent(eventId)}/workflow`",
            self.source,
        )

    def test_mobile_contract_does_not_narrow_action_code_to_local_enum(self):
        self.assertNotIn("action_code: 'release_", self.source)
        self.assertNotIn("type TrackedEventWorkflowActionCode", self.source)


if __name__ == "__main__":
    unittest.main()
