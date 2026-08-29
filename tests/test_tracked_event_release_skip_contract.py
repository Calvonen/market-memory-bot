from __future__ import annotations

import unittest
from pathlib import Path


API_PATH = Path("trading_system/api.py")
ROUTER_PATH = Path("trading_system/tracked_event_release_skip_api.py")
SERVICE_PATH = Path("trading_system/tracked_event_release_skip.py")


class TrackedEventReleaseSkipContractTests(unittest.TestCase):
    def test_backend_exposes_control_authenticated_tracked_event_skip_route(self) -> None:
        router = ROUTER_PATH.read_text(encoding="utf-8")

        self.assertIn('/api/v1/tracked-events/{event_id}/release-skip', router)
        self.assertIn('@router.post', router)
        self.assertIn('X-MarketAI-Control-Key', router)
        self.assertIn('X-MarketAI-Actor', router)
        self.assertNotIn('X-Admin-Token', router)

    def test_skip_requires_audited_reason_and_canonical_release_identity(self) -> None:
        service = SERVICE_PATH.read_text(encoding="utf-8")

        self.assertIn('canonical_release_event_id', service)
        self.assertIn('reason', service.lower())
        self.assertIn('actor', service.lower())
        self.assertIn('release_event_id', service)

    def test_skip_is_release_only_and_does_not_create_trading_work(self) -> None:
        source = ROUTER_PATH.read_text(encoding="utf-8") + SERVICE_PATH.read_text(encoding="utf-8")

        self.assertNotIn('paper_trade', source)
        self.assertNotIn('run_post_release_paper', source)
        self.assertNotIn('trading-task', source)
        self.assertNotIn('broker', source.lower())

    def test_router_is_wired_into_main_api(self) -> None:
        api = API_PATH.read_text(encoding="utf-8")

        self.assertIn('tracked_event_release_skip', api)
        self.assertIn('build_tracked_event_release_skip_router', api)


if __name__ == "__main__":
    unittest.main()
