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
        return self._run_main_with_argv_full(argv)["to_date"]

    def _run_main_with_argv_full(self, argv: list[str]) -> dict[str, date]:
        captured: dict[str, date] = {}

        def fake_run_sync(*, provider, repository, from_date, to_date):
            captured["from_date"] = from_date
            captured["to_date"] = to_date
            return CalendarSyncResult()

        fake_provider = SimpleNamespace(name="finnhub")
        with patch.object(calendar_sync_worker, "run_sync", side_effect=fake_run_sync), patch.object(
            calendar_sync_worker.FinnhubEarningsCalendarProvider, "from_env", return_value=fake_provider
        ), patch.object(
            calendar_sync_worker.SupabaseCalendarEventRepository, "from_env", return_value=object()
        ), patch("sys.argv", ["calendar_sync_worker.py", *argv]):
            calendar_sync_worker.main()

        return captured

    def test_default_invocation_fetches_at_most_the_max_lookahead_plus_the_skew_pad(self) -> None:
        to_date = self._run_main_with_argv([])
        self.assertEqual(
            to_date,
            date.today()
            + timedelta(
                days=MAX_CALENDAR_LOOKAHEAD_DAYS
                + calendar_sync_worker.INGESTION_LOOKAHEAD_PADDING_DAYS
            ),
        )

    def test_a_lookahead_beyond_the_cap_is_clamped_not_honored(self) -> None:
        to_date = self._run_main_with_argv(["--lookahead-days", "90"])
        self.assertEqual(
            to_date,
            date.today()
            + timedelta(
                days=MAX_CALENDAR_LOOKAHEAD_DAYS
                + calendar_sync_worker.INGESTION_LOOKAHEAD_PADDING_DAYS
            ),
        )

    def test_a_lookahead_within_the_cap_is_honored_exactly_plus_the_skew_pad(self) -> None:
        to_date = self._run_main_with_argv(["--lookahead-days", "7"])
        self.assertEqual(
            to_date, date.today() + timedelta(days=7 + calendar_sync_worker.INGESTION_LOOKAHEAD_PADDING_DAYS)
        )

    def test_a_lookahead_exactly_at_the_cap_is_not_clamped_further(self) -> None:
        to_date = self._run_main_with_argv(
            ["--lookahead-days", str(MAX_CALENDAR_LOOKAHEAD_DAYS)]
        )
        self.assertEqual(
            to_date,
            date.today()
            + timedelta(
                days=MAX_CALENDAR_LOOKAHEAD_DAYS
                + calendar_sync_worker.INGESTION_LOOKAHEAD_PADDING_DAYS
            ),
        )

    def test_ingestion_padding_is_at_least_one_day_forward_only(self) -> None:
        # "Pad ingestion window for timezone/date skew": a small,
        # explicit forward-only pad - never a return to a much wider
        # (e.g. 90-day) lookahead.
        self.assertGreaterEqual(calendar_sync_worker.INGESTION_LOOKAHEAD_PADDING_DAYS, 1)
        self.assertLess(calendar_sync_worker.INGESTION_LOOKAHEAD_PADDING_DAYS, 10)

    def test_ingestion_window_covers_a_device_one_day_ahead_of_this_host(self) -> None:
        # P2 regression: a device whose local calendar day is one day
        # ahead of this host's will request through its own
        # device_today + MAX_CALENDAR_LOOKAHEAD_DAYS (the widest, 30 pv,
        # chip) - in host-local terms, host_today + 31. The worker's own
        # "today" is always this host's, so its default ingestion window
        # must still reach that device-local boundary.
        to_date = self._run_main_with_argv([])
        host_today = date.today()
        device_today = host_today + timedelta(days=1)
        device_local_widest_request = device_today + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS)
        self.assertGreaterEqual(to_date, device_local_widest_request)

    def test_ingestion_padding_is_at_least_one_day_backward_only(self) -> None:
        # Symmetric to the forward pad: a small, explicit backward-only
        # pad - never a wholesale widening of the ingestion window.
        self.assertGreaterEqual(calendar_sync_worker.INGESTION_LOOKBEHIND_PADDING_DAYS, 1)
        self.assertLess(calendar_sync_worker.INGESTION_LOOKBEHIND_PADDING_DAYS, 10)

    def test_default_invocation_starts_one_day_before_this_host_today(self) -> None:
        captured = self._run_main_with_argv_full([])
        self.assertEqual(
            captured["from_date"],
            date.today() - timedelta(days=calendar_sync_worker.INGESTION_LOOKBEHIND_PADDING_DAYS),
        )

    def test_ingestion_window_covers_a_device_one_day_behind_this_host(self) -> None:
        # P2 regression: a device whose local calendar day is one day
        # *behind* this host's has its own "today" at host_today - 1, so
        # its widest (30 pv) request is
        # device_today -> device_today + 30, i.e.
        # host_today - 1 -> host_today + 29 in host-local terms - a day
        # earlier than the worker's from_date would otherwise ever reach.
        captured = self._run_main_with_argv_full([])
        host_today = date.today()
        device_today = host_today - timedelta(days=1)
        device_local_window_start = device_today
        device_local_window_end = device_today + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS)
        self.assertLessEqual(captured["from_date"], device_local_window_start)
        self.assertGreaterEqual(captured["to_date"], device_local_window_end)

    def test_ingestion_window_covers_both_a_device_ahead_and_a_device_behind_simultaneously(self) -> None:
        # The forward and backward pads must both hold at once from a
        # single ingestion run - the worker doesn't know in advance which
        # direction (if any) a given device will be skewed, so one sync
        # must serve every device's widest (30 pv) request regardless of
        # which way its local day differs from this host's.
        captured = self._run_main_with_argv_full([])
        host_today = date.today()

        device_ahead_today = host_today + timedelta(days=1)
        self.assertLessEqual(captured["from_date"], device_ahead_today)
        self.assertGreaterEqual(
            captured["to_date"], device_ahead_today + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS)
        )

        device_behind_today = host_today - timedelta(days=1)
        self.assertLessEqual(captured["from_date"], device_behind_today)
        self.assertGreaterEqual(
            captured["to_date"], device_behind_today + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS)
        )


if __name__ == "__main__":
    unittest.main()
