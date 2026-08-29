from __future__ import annotations

import unittest
from pathlib import Path


API_PATH = Path("trading_system/api.py")


class TrackedEventReleaseSourceRouterWiringTests(unittest.TestCase):
    def test_main_api_wires_release_source_router_with_existing_dependencies(self) -> None:
        source = API_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "from trading_system.tracked_event_release_source_api import (\n"
            "    build_tracked_event_release_source_router,\n"
            ")",
            source,
        )
        self.assertIn(
            "app.include_router(\n"
            "        build_tracked_event_release_source_router(\n"
            "            require_read=require_read,\n"
            "            require_control=require_control,\n"
            "            get_tracked_event_repository=get_tracked_event_repository,\n"
            "            get_official_release_source_repository=get_official_release_source_repository,\n"
            "        )\n"
            "    )",
            source,
        )

    def test_wiring_does_not_add_a_parallel_auth_or_repository(self) -> None:
        source = API_PATH.read_text(encoding="utf-8")

        self.assertEqual(source.count("build_tracked_event_release_source_router("), 1)
        self.assertNotIn("MARKETAI_RELEASE_SOURCE", source)


if __name__ == "__main__":
    unittest.main()
