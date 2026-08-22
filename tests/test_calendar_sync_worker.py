"""Calendar range MVP: the sync worker must never fetch (and therefore
never store) more than MAX_LOOKAHEAD_DAYS, matching the cap
GET /api/v1/calendar/upcoming enforces (trading_system.api.
MAX_CALENDAR_LOOKAHEAD_DAYS) - regardless of a misconfigured
MARKETAI_CALENDAR_LOOKAHEAD_DAYS or --lookahead-days."""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from trading_system import calendar_sync_worker
from trading_system.api import MAX_CALENDAR_LOOKAHEAD_DAYS
from trading_system.calendar_repository import CalendarSyncResult


class CalendarSyncWorkerRangeTests(unittest.TestCase):
    def test_default_and_max_lookahead_match_the_api_cap(self) -> None:
        self.assertEqual(calendar_sync_worker.DEFAULT_LOOKAHEAD_DAYS, MAX_CALENDAR_LOOKAHEAD_DAYS)
        self.assertEqual(calendar_sync_worker.MAX_LOOKAHEAD_DAYS, MAX_CALENDAR_LOOKAHEAD_DAYS)

    def _run_main_with_argv(self, argv: list[str]) -> date:
        captured: dict[str, date] = {}

        def fake_run_sync(*, provider, repository, from_date, to_date):
            captured["to_date"] = to_date
            return CalendarSyncResult()

        fake_provider = SimpleNamespace(name="finnhub")
        with patch.object(calendar_sync_worker, "run_sync", side_effect=fake_run_sync), patch.object(
            calendar_sync_worker.FinnhubEarningsCalendarProvider, "from_env", return_value=fake_provider
        ), patch.object(
            calendar_sync_worker.SupabaseCalendarEventRepository, "from_env", return_value=object()
        ), patch("sys.argv", ["calendar_sync_worker.py", *argv]):
            calendar_sync_worker.main()

        return captured["to_date"]

    def test_default_invocation_fetches_at_most_the_max_lookahead(self) -> None:
        to_date = self._run_main_with_argv([])
        self.assertEqual(to_date, date.today() + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS))

    def test_a_lookahead_beyond_the_cap_is_clamped_not_honored(self) -> None:
        to_date = self._run_main_with_argv(["--lookahead-days", "90"])
        self.assertEqual(to_date, date.today() + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS))

    def test_a_lookahead_within_the_cap_is_honored_exactly(self) -> None:
        to_date = self._run_main_with_argv(["--lookahead-days", "7"])
        self.assertEqual(to_date, date.today() + timedelta(days=7))

    def test_a_lookahead_exactly_at_the_cap_is_not_clamped_further(self) -> None:
        to_date = self._run_main_with_argv(
            ["--lookahead-days", str(MAX_CALENDAR_LOOKAHEAD_DAYS)]
        )
        self.assertEqual(to_date, date.today() + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS))


if __name__ == "__main__":
    unittest.main()
