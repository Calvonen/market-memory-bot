from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from trading_system.calendar_release_worker import (
    CalendarReleaseTarget,
    _default_automatic_release_provider,
)
from trading_system.global_release_discovery import FinnhubOfficialResultsProvider


class CalendarReleaseWorkerGlobalMarketRoutingTests(unittest.TestCase):
    def _target(self, market: str) -> CalendarReleaseTarget:
        return CalendarReleaseTarget(
            calendar_event_id=None,
            event_id="tracked:633c9941-8426-4dda-93b8-d829d0d68605",
            ticker="SLHN.ZU",
            scheduled_date=date(2026, 9, 1),
            market=market,
            tracked_event_id="633c9941-8426-4dda-93b8-d829d0d68605",
            company_name="Swiss Life Holding AG",
        )

    @patch.dict("os.environ", {"FINNHUB_API_KEY": "test-key"}, clear=False)
    def test_unknown_market_does_not_receive_global_provider(self):
        self.assertIsNone(_default_automatic_release_provider(self._target("UNKNOWN")))
        self.assertIsNone(_default_automatic_release_provider(self._target("")))

    @patch.dict("os.environ", {"FINNHUB_API_KEY": "test-key"}, clear=False)
    def test_known_non_us_market_receives_global_provider(self):
        provider = _default_automatic_release_provider(self._target("SWITZERLAND"))
        self.assertIsInstance(provider, FinnhubOfficialResultsProvider)
        self.assertEqual(provider.market, "SWITZERLAND")
        self.assertEqual(provider.company_name, "Swiss Life Holding AG")


if __name__ == "__main__":
    unittest.main()
