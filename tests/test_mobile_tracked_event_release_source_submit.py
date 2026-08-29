from __future__ import annotations

import unittest
from pathlib import Path


API_PATH = Path("mobile/src/services/api.ts")
SERVICE_PATH = Path("mobile/src/services/tracked-events.ts")
SCREEN_PATH = Path("mobile/src/app/tracked-events/[eventId]/release.tsx")


class MobileTrackedEventReleaseSourceSubmitTests(unittest.TestCase):
    def test_mobile_reuses_existing_control_auth_for_put(self) -> None:
        api_source = API_PATH.read_text(encoding="utf-8")
        service_source = SERVICE_PATH.read_text(encoding="utf-8")

        self.assertIn("X-MarketAI-Control-Key", api_source)
        self.assertIn("putTrackedEventReleaseSource", service_source)
        self.assertIn("/release-source", service_source)
        self.assertNotIn("X-Admin-Token", service_source)
        self.assertNotIn("EXPO_PUBLIC_MARKETAI_ADMIN", service_source)

    def test_submit_uses_loaded_canonical_version_and_direct_url(self) -> None:
        screen_source = SCREEN_PATH.read_text(encoding="utf-8")

        self.assertIn("expected_version: releaseSource.version", screen_source)
        self.assertIn("source_kind: 'direct_url'", screen_source)
        self.assertIn("setReleaseSource(saved)", screen_source)

    def test_submit_does_not_start_ingestion_or_create_trading_task(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8") + SERVICE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("/ingest", source)
        self.assertNotIn("/retry", source)
        self.assertNotIn("trading-task", source)
        self.assertNotIn("paper-run", source)


if __name__ == "__main__":
    unittest.main()
