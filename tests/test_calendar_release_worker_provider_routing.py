from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from trading_system.calendar_release_worker import (
    CalendarReleaseTarget,
    run_calendar_release_ingestion_once,
)
from trading_system.models import EventExpectation
from trading_system.release_worker import IngestionResult


EVENT_ID = "tracked:633c9941-8426-4dda-93b8-d829d0d68605"


class _Targets:
    def list_targets(self, *, start_date, end_date):
        return (
            CalendarReleaseTarget(
                calendar_event_id=None,
                event_id=EVENT_ID,
                ticker="SLHN.ZU",
                scheduled_date=date(2026, 9, 1),
                market="SWITZERLAND",
                tracked_event_id="633c9941-8426-4dda-93b8-d829d0d68605",
            ),
        )

    def ensure_release_shell(self, target):
        return target.event_id


class _Expectations:
    def get(self, event_id):
        if event_id != EVENT_ID:
            return None
        return EventExpectation(
            event_id=EVENT_ID,
            instrument="SLHN.ZU",
            event_name="Swiss Life Half-year results 2026",
            scheduled_date=date(2026, 9, 1),
            source_name="manual:swiss-life-e2e-pre-event",
            version=2,
        )


def _clock() -> datetime:
    return datetime(2026, 9, 1, 7, 0, tzinfo=UTC)


class CalendarReleaseWorkerProviderRoutingTests(unittest.TestCase):
    def test_non_us_target_can_use_automatic_provider_factory(self):
        releases = MagicMock()
        releases.has_analysis_for_event_version.return_value = False
        releases.latest_run.return_value = None
        official_sources = MagicMock()
        official_sources.get.return_value = None
        provider = MagicMock()
        provider.name = "global_official_results"
        factory = MagicMock(return_value=provider)

        with patch("trading_system.calendar_release_worker.EventReleaseMonitor") as monitor_type:
            monitor_type.return_value.run_once.return_value = IngestionResult(
                event_id=EVENT_ID,
                status="no_release",
                message=None,
            )
            results = run_calendar_release_ingestion_once(
                targets=_Targets(),
                expectations=_Expectations(),
                releases=releases,
                analyzer=MagicMock(),
                official_sources=official_sources,
                automatic_provider_factory=factory,
                clock=_clock,
            )

        self.assertEqual(results[0].status, "no_release")
        factory.assert_called_once()
        routed_target = factory.call_args.args[0]
        self.assertEqual(routed_target.market, "SWITZERLAND")
        self.assertEqual(routed_target.ticker, "SLHN.ZU")
        monitor_type.assert_called_once()
        self.assertIs(monitor_type.call_args.kwargs["provider"], provider)
        releases.record_run.assert_not_called()

    def test_missing_provider_still_fails_closed(self):
        releases = MagicMock()
        releases.has_analysis_for_event_version.return_value = False
        releases.latest_run.return_value = None
        official_sources = MagicMock()
        official_sources.get.return_value = None

        with patch("trading_system.calendar_release_worker.EventReleaseMonitor") as monitor_type:
            results = run_calendar_release_ingestion_once(
                targets=_Targets(),
                expectations=_Expectations(),
                releases=releases,
                analyzer=MagicMock(),
                official_sources=official_sources,
                automatic_provider_factory=lambda target: None,
                clock=_clock,
            )

        self.assertEqual(results[0].status, "missing_official_source")
        monitor_type.assert_not_called()
        releases.record_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
