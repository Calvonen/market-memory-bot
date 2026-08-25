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


class _Targets:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.calls = []

    def list_targets(self, *, start_date, end_date):
        self.calls.append((start_date, end_date))
        return self.rows


class _Expectations:
    def __init__(self, by_id):
        self.by_id = dict(by_id)

    def get(self, event_id):
        return self.by_id.get(event_id)


class _Releases:
    def __init__(self, analyzed=False):
        self.analyzed = analyzed
        self.analysis_checks = []

    def has_analysis_for_event_version(self, *, event_id, expectation_version):
        self.analysis_checks.append((event_id, expectation_version))
        return self.analyzed


class CalendarReleaseWorkerTests(unittest.TestCase):
    EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"

    def _target(self, *, ticker="DKS", scheduled=date(2026, 8, 25)):
        return CalendarReleaseTarget(
            calendar_event_id="22648076-6e43-40fc-ac6e-f57a79ceee31",
            event_id=self.EVENT_ID,
            ticker=ticker,
            scheduled_date=scheduled,
        )

    def _expectation(self, *, ticker="DKS", scheduled=date(2026, 8, 25)):
        return EventExpectation(
            event_id=self.EVENT_ID,
            instrument=ticker,
            event_name="DICK'S SPORTING GOODS INC earnings",
            scheduled_date=scheduled,
            source_name="calendar:finnhub:automatic-release-shell",
            version=1,
        )

    def test_runs_sec_monitor_for_valid_us_calendar_shell(self):
        targets = _Targets([self._target()])
        expectations = _Expectations({self.EVENT_ID: self._expectation()})
        releases = _Releases()
        fake_monitor = MagicMock()
        fake_monitor.run_once.return_value = IngestionResult(
            status="analyzed",
            message="AI provider=groq",
        )

        with patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as provider_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            return_value=fake_monitor,
        ) as monitor_cls:
            results = run_calendar_release_ingestion_once(
                targets=targets,
                expectations=expectations,
                releases=releases,
                analyzer=MagicMock(),
                clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            )

        self.assertEqual(results[0].status, "analyzed")
        provider_cls.assert_called_once_with(
            ticker="DKS",
            scheduled_date=date(2026, 8, 25),
        )
        monitor_cls.assert_called_once()
        fake_monitor.run_once.assert_called_once_with(self.EVENT_ID)
        self.assertEqual(
            targets.calls,
            [(date(2026, 8, 24), date(2026, 8, 25))],
        )
        self.assertEqual(releases.analysis_checks, [(self.EVENT_ID, 1)])

    def test_missing_release_shell_fails_closed_without_provider(self):
        with patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as provider_cls:
            results = run_calendar_release_ingestion_once(
                targets=_Targets([self._target()]),
                expectations=_Expectations({}),
                releases=_Releases(),
                analyzer=MagicMock(),
                clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            )

        self.assertEqual(results[0].status, "missing_release_shell")
        provider_cls.assert_not_called()

    def test_identity_drift_fails_closed_without_provider(self):
        with patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as provider_cls:
            results = run_calendar_release_ingestion_once(
                targets=_Targets([self._target()]),
                expectations=_Expectations(
                    {self.EVENT_ID: self._expectation(ticker="WRONG")}
                ),
                releases=_Releases(),
                analyzer=MagicMock(),
                clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            )

        self.assertEqual(results[0].status, "identity_conflict")
        provider_cls.assert_not_called()

    def test_already_analyzed_version_skips_sec_and_ai_work(self):
        releases = _Releases(analyzed=True)
        with patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as provider_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor"
        ) as monitor_cls:
            results = run_calendar_release_ingestion_once(
                targets=_Targets([self._target()]),
                expectations=_Expectations({self.EVENT_ID: self._expectation()}),
                releases=releases,
                analyzer=MagicMock(),
                clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            )

        self.assertEqual(results[0].status, "already_analyzed")
        provider_cls.assert_not_called()
        monitor_cls.assert_not_called()

    def test_one_event_error_does_not_starve_later_targets(self):
        second_id = "calendar:00000000-0000-0000-0000-000000000002"
        first = self._target()
        second = CalendarReleaseTarget(
            calendar_event_id="00000000-0000-0000-0000-000000000002",
            event_id=second_id,
            ticker="ABC",
            scheduled_date=date(2026, 8, 25),
        )
        expectations = _Expectations(
            {
                self.EVENT_ID: self._expectation(),
                second_id: EventExpectation(
                    event_id=second_id,
                    instrument="ABC",
                    event_name="ABC earnings",
                    scheduled_date=date(2026, 8, 25),
                ),
            }
        )
        first_monitor = MagicMock()
        first_monitor.run_once.side_effect = RuntimeError("SEC transient failure")
        second_monitor = MagicMock()
        second_monitor.run_once.return_value = IngestionResult(status="no_release")

        with patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ), patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            side_effect=[first_monitor, second_monitor],
        ):
            results = run_calendar_release_ingestion_once(
                targets=_Targets([first, second]),
                expectations=expectations,
                releases=_Releases(),
                analyzer=MagicMock(),
                clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            )

        self.assertEqual([row.status for row in results], ["error", "no_release"])
        second_monitor.run_once.assert_called_once_with(second_id)


if __name__ == "__main__":
    unittest.main()
