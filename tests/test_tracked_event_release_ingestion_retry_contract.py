from __future__ import annotations

import unittest
from pathlib import Path


API_PATH = Path("trading_system/api.py")
ROUTER_PATH = Path("trading_system/tracked_event_release_ingestion_api.py")
SERVICE_PATH = Path("trading_system/tracked_event_release_ingestion.py")


class TrackedEventReleaseIngestionRetryContractTests(unittest.TestCase):
    def test_backend_exposes_control_authenticated_tracked_event_retry_route(self) -> None:
        router = ROUTER_PATH.read_text(encoding="utf-8")

        self.assertIn('/api/v1/tracked-events/{event_id}/release-ingestion', router)
        self.assertIn('@router.post', router)
        self.assertIn('X-MarketAI-Control-Key', router)
        self.assertIn('X-MarketAI-Actor', router)
        self.assertNotIn('X-Admin-Token', router)

    def test_retry_maps_tracked_event_to_canonical_release_identity(self) -> None:
        service = SERVICE_PATH.read_text(encoding="utf-8")

        self.assertIn('canonical_release_event_id', service)
        self.assertIn('official_release', service.lower())
        self.assertIn('ManualOfficialReleaseProvider', service)
        self.assertIn('ResultsPageOfficialReleaseProvider', service)
        self.assertIn('EventReleaseMonitor', service)

    def test_retry_is_release_only_and_does_not_create_trading_work(self) -> None:
        source = ROUTER_PATH.read_text(encoding="utf-8") + SERVICE_PATH.read_text(encoding="utf-8")

        self.assertNotIn('paper_trade', source)
        self.assertNotIn('run_post_release_paper', source)
        self.assertNotIn('trading-task', source)
        self.assertNotIn('/skip', source)
        self.assertNotIn('pdf upload', source.lower())

    def test_router_is_wired_into_main_api(self) -> None:
        api = API_PATH.read_text(encoding="utf-8")

        self.assertIn('tracked_event_release_ingestion', api)
        self.assertIn('build_tracked_event_release_ingestion_router', api)


if __name__ == "__main__":
    unittest.main()
