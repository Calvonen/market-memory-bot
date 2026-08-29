from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from trading_system.calendar_release_worker import (
    ACTION_REQUIRED_PROVIDER,
    CalendarReleaseTarget,
    run_calendar_release_ingestion_once,
)
from trading_system.models import EventExpectation


EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"


class _Targets:
    def __init__(
        self,
        target: CalendarReleaseTarget,
        *,
        ensure_error: Exception | None = None,
    ) -> None:
        self.target = target
        self.ensure_error = ensure_error

    def list_targets(self, *, start_date, end_date):
        return (self.target,)

    def ensure_release_shell(self, target):
        if self.ensure_error is not None:
            raise self.ensure_error
        return target.event_id


class _Expectations:
    def __init__(self, expectation: EventExpectation | None) -> None:
        self.expectation = expectation
        self.calls = []

    def get(self, event_id: str):
        self.calls.append(event_id)
        return self.expectation if event_id == EVENT_ID else None


def _target(*, ticker: str = "DKS", market: str = "NASDAQ") -> CalendarReleaseTarget:
    return CalendarReleaseTarget(
        calendar_event_id="22648076-6e43-40fc-ac6e-f57a79ceee31",
        event_id=EVENT_ID,
        ticker=ticker,
        scheduled_date=date(2026, 8, 25),
        market=market,
    )


def _expectation(*, ticker: str = "DKS") -> EventExpectation:
    return EventExpectation(
        event_id=EVENT_ID,
        instrument=ticker,
        event_name="DICK'S SPORTING GOODS INC earnings",
        scheduled_date=date(2026, 8, 25),
        source_name="calendar:finnhub:automatic-release-shell",
        version=1,
    )


def _clock() -> datetime:
    return datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


class CalendarReleaseWorkerActionRequiredPersistenceTests(unittest.TestCase):
    def _releases(self) -> MagicMock:
        releases = MagicMock()
        releases.has_analysis_for_event_version.return_value = False
        releases.latest_run.return_value = None
        return releases

    def test_shell_rpc_identity_conflict_is_persisted_before_expectation_lookup(self):
        releases = self._releases()
        expectations = _Expectations(_expectation())
        targets = _Targets(
            _target(),
            ensure_error=RuntimeError(
                "tracked_release_shell_identity_conflict: instrument mismatch"
            ),
        )

        results = run_calendar_release_ingestion_once(
            targets=targets,
            expectations=expectations,
            releases=releases,
            analyzer=MagicMock(),
            clock=_clock,
        )

        self.assertEqual(results[0].status, "identity_conflict")
        self.assertEqual(expectations.calls, [])
        releases.record_run.assert_called_once_with(
            event_id=EVENT_ID,
            provider=ACTION_REQUIRED_PROVIDER,
            status="error",
            error_message=(
                "action_required: canonical release-shell identity conflicts with "
                "tracked-event identity"
            ),
        )

    def test_calendar_binding_identity_conflict_is_persisted_before_expectation_lookup(self):
        releases = self._releases()
        expectations = _Expectations(_expectation())
        targets = _Targets(
            _target(),
            ensure_error=RuntimeError(
                "tracked_release_calendar_binding_identity_conflict: instrument mismatch"
            ),
        )

        results = run_calendar_release_ingestion_once(
            targets=targets,
            expectations=expectations,
            releases=releases,
            analyzer=MagicMock(),
            clock=_clock,
        )

        self.assertEqual(results[0].status, "identity_conflict")
        self.assertEqual(expectations.calls, [])
        releases.record_run.assert_called_once_with(
            event_id=EVENT_ID,
            provider=ACTION_REQUIRED_PROVIDER,
            status="error",
            error_message=(
                "action_required: canonical release-shell identity conflicts with "
                "tracked-event identity"
            ),
        )

    def test_unrelated_shell_rpc_failure_remains_retryable_error(self):
        releases = self._releases()
        expectations = _Expectations(_expectation())
        targets = _Targets(
            _target(),
            ensure_error=RuntimeError("temporary Supabase timeout"),
        )

        results = run_calendar_release_ingestion_once(
            targets=targets,
            expectations=expectations,
            releases=releases,
            analyzer=MagicMock(),
            clock=_clock,
        )

        self.assertEqual(results[0].status, "error")
        self.assertIn("temporary Supabase timeout", results[0].message or "")
        self.assertEqual(expectations.calls, [])
        releases.record_run.assert_not_called()

    def test_missing_release_shell_is_persisted_as_action_required_error(self):
        releases = self._releases()

        with patch("trading_system.calendar_release_worker.SecEdgarResultsProvider") as provider:
            results = run_calendar_release_ingestion_once(
                targets=_Targets(_target()),
                expectations=_Expectations(None),
                releases=releases,
                analyzer=MagicMock(),
                clock=_clock,
            )

        self.assertEqual(results[0].status, "missing_release_shell")
        releases.record_run.assert_called_once_with(
            event_id=EVENT_ID,
            provider=ACTION_REQUIRED_PROVIDER,
            status="error",
            error_message="action_required: current_event_expectations row is missing",
        )
        provider.assert_not_called()

    def test_identity_conflict_is_persisted_before_provider_selection(self):
        releases = self._releases()

        with patch("trading_system.calendar_release_worker.SecEdgarResultsProvider") as provider:
            results = run_calendar_release_ingestion_once(
                targets=_Targets(_target()),
                expectations=_Expectations(_expectation(ticker="WRONG")),
                releases=releases,
                analyzer=MagicMock(),
                clock=_clock,
            )

        self.assertEqual(results[0].status, "identity_conflict")
        releases.record_run.assert_called_once_with(
            event_id=EVENT_ID,
            provider=ACTION_REQUIRED_PROVIDER,
            status="error",
            error_message=(
                "action_required: release-shell instrument differs from "
                "tracked-event instrument"
            ),
        )
        provider.assert_not_called()

    def test_repaired_release_shell_supersedes_prior_identity_blocker_once(self):
        releases = self._releases()
        releases.has_analysis_for_event_version.return_value = True
        releases.latest_run.return_value = {
            "provider": ACTION_REQUIRED_PROVIDER,
            "status": "error",
            "error_message": (
                "action_required: canonical release-shell identity conflicts with "
                "tracked-event identity"
            ),
        }

        results = run_calendar_release_ingestion_once(
            targets=_Targets(_target()),
            expectations=_Expectations(_expectation()),
            releases=releases,
            analyzer=MagicMock(),
            clock=_clock,
        )

        self.assertEqual(results[0].status, "already_analyzed")
        releases.record_run.assert_called_once_with(
            event_id=EVENT_ID,
            provider=ACTION_REQUIRED_PROVIDER,
            status="validated",
            error_message=None,
        )

    def test_missing_non_us_official_source_is_persisted_for_workflow_readiness(self):
        releases = self._releases()
        official_sources = MagicMock()
        official_sources.get.return_value = None

        with patch("trading_system.calendar_release_worker.EventReleaseMonitor") as monitor:
            results = run_calendar_release_ingestion_once(
                targets=_Targets(_target(ticker="HVN.ASX", market="SYDNEY")),
                expectations=_Expectations(_expectation(ticker="HVN.ASX")),
                releases=releases,
                analyzer=MagicMock(),
                official_sources=official_sources,
                clock=_clock,
            )

        self.assertEqual(results[0].status, "missing_official_source")
        releases.record_run.assert_called_once_with(
            event_id=EVENT_ID,
            provider=ACTION_REQUIRED_PROVIDER,
            status="error",
            error_message=(
                "action_required: earnings target outside approved US market labels "
                "requires an approved official release source"
            ),
        )
        monitor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
