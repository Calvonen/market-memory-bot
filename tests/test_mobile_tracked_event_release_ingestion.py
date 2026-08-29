from __future__ import annotations

import unittest
from pathlib import Path


API_PATH = Path("mobile/src/services/api.ts")
SERVICE_PATH = Path("mobile/src/services/tracked-events.ts")
SCREEN_PATH = Path("mobile/src/app/tracked-events/[eventId]/release.tsx")


class MobileTrackedEventReleaseIngestionTests(unittest.TestCase):
    def test_service_uses_control_authenticated_canonical_ingestion_route(self) -> None:
        api_source = API_PATH.read_text(encoding="utf-8")
        service_source = SERVICE_PATH.read_text(encoding="utf-8")

        self.assertIn("apiControlPost", api_source)
        self.assertIn("ingestTrackedEventRelease", service_source)
        self.assertIn("/release-ingestion", service_source)
        self.assertIn("X-MarketAI-Actor", service_source)
        self.assertNotIn("X-Admin-Token", service_source)

    def test_screen_gates_ingestion_on_canonical_release_action_and_active_source(self) -> None:
        screen = SCREEN_PATH.read_text(encoding="utf-8")

        self.assertIn("releaseStep?.status === 'action_required'", screen)
        self.assertIn("releaseStep.action_target === 'release'", screen)
        self.assertIn("releaseSource?.active", screen)
        self.assertIn("Käsittele julkaisu", screen)

    def test_ingestion_refreshes_canonical_source_and_workflow(self) -> None:
        screen = SCREEN_PATH.read_text(encoding="utf-8")

        call = "await ingestTrackedEventRelease(submittedEventId, normalizedActor)"
        self.assertIn(call, screen)
        after_call = screen.split(call, 1)[1]
        self.assertIn("getTrackedEventReleaseSource(submittedEventId)", after_call)
        self.assertIn("getTrackedEventWorkflow(submittedEventId)", after_call)
        self.assertIn("setWorkflow(currentWorkflow)", after_call)

    def test_mobile_ingestion_does_not_add_skip_or_trading_paths(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8") + SERVICE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("/release-skip", source)
        self.assertNotIn("paper-run", source)
        self.assertNotIn("trading-task", source)
        self.assertNotIn("broker", source.lower())


if __name__ == "__main__":
    unittest.main()
