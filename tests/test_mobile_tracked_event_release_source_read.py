from __future__ import annotations

import unittest
from pathlib import Path


SERVICE_PATH = Path("mobile/src/services/tracked-events.ts")
SCREEN_PATH = Path("mobile/src/app/tracked-events/[eventId]/release.tsx")


class MobileTrackedEventReleaseSourceReadTests(unittest.TestCase):
    def test_service_reads_canonical_tracked_event_release_source_endpoint(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")

        self.assertIn("export type TrackedEventReleaseSource = {", source)
        self.assertIn("export function getTrackedEventReleaseSource(eventId: string)", source)
        self.assertIn("/api/v1/tracked-events/${encodeURIComponent(eventId)}/release-source", source)
        self.assertIn("return apiGet<TrackedEventReleaseSource>", source)

    def test_release_screen_is_read_only_and_renders_source_state(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8")

        self.assertIn("getTrackedEventReleaseSource(eventId)", source)
        self.assertIn("releaseSource.active", source)
        self.assertIn("releaseSource.source_url", source)
        self.assertNotIn("apiPost", source)
        self.assertNotIn("apiPut", source)
        self.assertNotIn("apiDelete", source)
        self.assertNotIn("EXPO_PUBLIC_MARKETAI_CONTROL_API_KEY", source)


if __name__ == "__main__":
    unittest.main()
