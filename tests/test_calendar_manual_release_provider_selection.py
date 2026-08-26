from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from trading_system.calendar_release_worker import (
    CalendarReleaseTarget,
    run_calendar_release_ingestion_once,
)
from trading_system.models import EventExpectation
from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.release_worker import IngestionResult


EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"
SCHEDULED = date(2026, 8, 25)


class _Targets:
    def list_targets(self, *, start_date, end_date):
        return (
            CalendarReleaseTarget(
                calendar_event_id=EVENT_ID.removeprefix("calendar:"),
                event_id=EVENT_ID,
                ticker="DKS",
                scheduled_date=SCHEDULED,
            ),
        )


class _Expectations:
    def get(self, event_id):
        if event_id != EVENT_ID:
            return None
        return EventExpectation(
            event_id=EVENT_ID,
            instrument="DKS",
            event_name="DKS earnings",
            scheduled_date=SCHEDULED,
            version=1,
        )


class _Releases:
    def __init__(self, *, analyzed=False):
        self.analyzed = analyzed

    def has_analysis_for_event_version(self, *, event_id, expectation_version):
        return self.analyzed


class _OfficialSources:
    def __init__(self, source=None, *, error=None):
        self.source = source
        self.error = error
        self.calls = []

    def get(self, event_id):
        self.calls.append(event_id)
        if self.error is not None:
            raise self.error
        return self.source


class CalendarManualReleaseProviderSelectionTests(unittest.TestCase):
    def _run(self, *, official_sources, releases=None):
        return run_calendar_release_ingestion_once(
            targets=_Targets(),
            expectations=_Expectations(),
            releases=releases or _Releases(),
            analyzer=MagicMock(),
            official_sources=official_sources,
            clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        )

    def test_approved_manual_source_replaces_sec_provider_for_event(self):
        source = OfficialReleaseSource(
            event_id=EVENT_ID,
            source_kind="direct_url",
            source_url="https://investor.example.com/results.pdf",
            source_title="FY2026 results",
            version=1,
        )
        official_sources = _OfficialSources(source)
        fake_monitor = MagicMock()
        fake_monitor.run_once.return_value = IngestionResult(status="no_release")
        manual_provider = MagicMock()

        with patch(
            "trading_system.calendar_release_worker.ManualOfficialReleaseProvider",
            return_value=manual_provider,
        ) as manual_cls, patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as sec_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            return_value=fake_monitor,
        ) as monitor_cls:
            results = self._run(official_sources=official_sources)

        self.assertEqual(results[0].status, "no_release")
        self.assertEqual(official_sources.calls, [EVENT_ID])
        manual_cls.assert_called_once_with(source)
        sec_cls.assert_not_called()
        self.assertIs(monitor_cls.call_args.kwargs["provider"], manual_provider)
        fake_monitor.run_once.assert_called_once_with(EVENT_ID)

    def test_missing_manual_source_preserves_existing_sec_fallback(self):
        official_sources = _OfficialSources(None)
        fake_monitor = MagicMock()
        fake_monitor.run_once.return_value = IngestionResult(status="no_release")
        sec_provider = MagicMock()

        with patch(
            "trading_system.calendar_release_worker.ManualOfficialReleaseProvider"
        ) as manual_cls, patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider",
            return_value=sec_provider,
        ) as sec_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            return_value=fake_monitor,
        ) as monitor_cls:
            results = self._run(official_sources=official_sources)

        self.assertEqual(results[0].status, "no_release")
        manual_cls.assert_not_called()
        sec_cls.assert_called_once_with(ticker="DKS", scheduled_date=SCHEDULED)
        self.assertIs(monitor_cls.call_args.kwargs["provider"], sec_provider)

    def test_manual_source_lookup_failure_fails_event_closed_without_sec_fallback(self):
        official_sources = _OfficialSources(error=RuntimeError("source repository unavailable"))

        with patch(
            "trading_system.calendar_release_worker.ManualOfficialReleaseProvider"
        ) as manual_cls, patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as sec_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor"
        ) as monitor_cls:
            results = self._run(official_sources=official_sources)

        self.assertEqual(results[0].status, "error")
        self.assertIn("source repository unavailable", results[0].message or "")
        manual_cls.assert_not_called()
        sec_cls.assert_not_called()
        monitor_cls.assert_not_called()

    def test_already_analyzed_event_skips_manual_source_lookup(self):
        official_sources = _OfficialSources(
            OfficialReleaseSource(
                event_id=EVENT_ID,
                source_kind="direct_url",
                source_url="https://investor.example.com/results.pdf",
                version=1,
            )
        )

        results = self._run(
            official_sources=official_sources,
            releases=_Releases(analyzed=True),
        )

        self.assertEqual(results[0].status, "already_analyzed")
        self.assertEqual(official_sources.calls, [])


if __name__ == "__main__":
    unittest.main()
