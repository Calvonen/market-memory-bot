from __future__ import annotations

import unittest
from pathlib import Path


ROUTER_PATH = Path("trading_system/tracked_event_release_source_api.py")
API_PATH = Path("trading_system/api.py")


class TrackedEventReleaseSourceWriteWiringTests(unittest.TestCase):
    def test_router_exposes_control_authenticated_tracked_event_put(self) -> None:
        source = ROUTER_PATH.read_text(encoding="utf-8")

        self.assertIn(
            '@router.put("/api/v1/tracked-events/{event_id}/release-source")',
            source,
        )
        self.assertIn("require_control:", source)
        self.assertIn('alias="X-MarketAI-Control-Key"', source)
        self.assertNotIn('alias="X-Admin-Token"', source)

    def test_write_maps_tracked_event_to_canonical_release_identity_and_uses_cas(self) -> None:
        source = ROUTER_PATH.read_text(encoding="utf-8")

        self.assertIn("release_event_id = canonical_release_event_id(event)", source)
        self.assertIn("OfficialReleaseSource(", source)
        self.assertIn("event_id=release_event_id", source)
        self.assertIn("expected_version=request.expected_version", source)
        self.assertIn("actor=actor", source)

    def test_main_api_wires_existing_control_auth_into_router(self) -> None:
        source = API_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "build_tracked_event_release_source_router(\n"
            "            require_read=require_read,\n"
            "            require_control=require_control,",
            source,
        )


if __name__ == "__main__":
    unittest.main()
