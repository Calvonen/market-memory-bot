from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from trading_system.calendar_release_worker import (
    DATE_ONLY_OVERDUE_GRACE_HOURS,
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
    def __init__(self, by_id, *, fail_event_id=None):
        self.by_id = dict(by_id)
        self.fail_event_id = fail_event_id

    def get(self, event_id):
        if event_id == self.fail_event_id:
            raise RuntimeError("expectation repository transient failure")
        return self.by_id.get(event_id)


class _Releases:
    def __init__(self, analyzed=False, *, fail_event_id=None):
        self.analyzed = analyzed
        self.fail_event_id = fail_event_id
        self.analysis_checks = []

    def has_analysis_for_event_version(self, *, event_id, expectation_version):
        self.analysis_checks.append((event_id, expectation_version))
        if event_id == self.fail_event_id:
            raise RuntimeError("analysis repository transient failure")
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

    def _expectation(self, *, ticker="DKS", scheduled=date(2026, 8, 25), version=1):
        return EventExpectation(
            event_id=self.EVENT_ID,
            instrument=ticker,
            event_name="DICK'S SPORTING GOODS INC earnings",
            scheduled_date=scheduled,
            source_name="calendar:finnhub:automatic-release-shell",
            version=version,
        )

    @staticmethod
    def _second_target_and_expectation():
        second_id = "calendar:00000000-0000-0000-0000-000000000002"
        second = CalendarReleaseTarget(
            calendar_event_id="00000000-0000-0000-0000-000000000002",
            event_id=second_id,
            ticker="ABC",
            scheduled_date=date(2026, 8, 25),
        )
        expectation = EventExpectation(
            event_id=second_id,
            instrument="ABC",
            event_name="ABC earnings",
            scheduled_date=date(2026, 8, 25),
        )
        return second_id, second, expectation

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
        self.assertEqual(
            monitor_cls.call_args.kwargs["overdue_grace_hours"],
            DATE_ONLY_OVERDUE_GRACE_HOURS,
        )
        self.assertEqual(DATE_ONLY_OVERDUE_GRACE_HOURS, 24.0)
        pinned = monitor_cls.call_args.kwargs["expectation_repository"]
        self.assertEqual(pinned.get(self.EVENT_ID).version, 1)
        fake_monitor.run_once.assert_called_once_with(self.EVENT_ID)
        self.assertEqual(
            targets.calls,
            [(date(2026, 8, 24), date(2026, 8, 25))],
        )
        self.assertEqual(releases.analysis_checks, [(self.EVENT_ID, 1)])

    def test_monitor_keeps_validated_expectation_if_source_changes_mid_run(self):
        original = self._expectation(version=1)
        expectations = _Expectations({self.EVENT_ID: original})
        releases = _Releases()
        captured_versions = []

        def monitor_factory(**kwargs):
            pinned = kwargs["expectation_repository"]
            fake_monitor = MagicMock()

            def run_once(event_id):
                expectations.by_id[event_id] = self._expectation(version=2)
                captured_versions.append(pinned.get(event_id).version)
                return IngestionResult(status="no_release")

            fake_monitor.run_once.side_effect = run_once
            return fake_monitor

        with patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ), patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            side_effect=monitor_factory,
        ):
            results = run_calendar_release_ingestion_once(
                targets=_Targets([self._target()]),
                expectations=expectations,
                releases=releases,
                analyzer=MagicMock(),
                clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            )

        self.assertEqual(results[0].status, "no_release")
        self.assertEqual(expectations.by_id[self.EVENT_ID].version, 2)
        self.assertEqual(captured_versions, [1])
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

    def test_monitor_error_does_not_starve_later_targets(self):
        second_id, second, second_expectation = self._second_target_and_expectation()
        expectations = _Expectations(
            {
                self.EVENT_ID: self._expectation(),
                second_id: second_expectation,
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
                targets=_Targets([self._target(), second]),
                expectations=expectations,
                releases=_Releases(),
                analyzer=MagicMock(),
                clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            )

        self.assertEqual([row.status for row in results], ["error", "no_release"])
        second_monitor.run_once.assert_called_once_with(second_id)

    def test_expectation_lookup_error_does_not_starve_later_targets(self):
        second_id, second, second_expectation = self._second_target_and_expectation()
        expectations = _Expectations(
            {second_id: second_expectation},
            fail_event_id=self.EVENT_ID,
        )
        second_monitor = MagicMock()
        second_monitor.run_once.return_value = IngestionResult(status="no_release")

        with patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ), patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            return_value=second_monitor,
        ):
            results = run_calendar_release_ingestion_once(
                targets=_Targets([self._target(), second]),
                expectations=expectations,
                releases=_Releases(),
                analyzer=MagicMock(),
                clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            )

        self.assertEqual([row.status for row in results], ["error", "no_release"])
        self.assertIn("expectation repository", results[0].message or "")
        second_monitor.run_once.assert_called_once_with(second_id)

    def test_analysis_lookup_error_does_not_starve_later_targets(self):
        second_id, second, second_expectation = self._second_target_and_expectation()
        expectations = _Expectations(
            {
                self.EVENT_ID: self._expectation(),
                second_id: second_expectation,
            }
        )
        second_monitor = MagicMock()
        second_monitor.run_once.return_value = IngestionResult(status="no_release")

        with patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ), patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            return_value=second_monitor,
        ):
            results = run_calendar_release_ingestion_once(
                targets=_Targets([self._target(), second]),
                expectations=expectations,
                releases=_Releases(fail_event_id=self.EVENT_ID),
                analyzer=MagicMock(),
                clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            )

        self.assertEqual([row.status for row in results], ["error", "no_release"])
        self.assertIn("analysis repository", results[0].message or "")
        second_monitor.run_once.assert_called_once_with(second_id)


if __name__ == "__main__":
    unittest.main()
