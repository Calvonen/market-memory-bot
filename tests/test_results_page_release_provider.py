from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from trading_system.calendar_release_worker import (
    CalendarReleaseTarget,
    run_calendar_release_ingestion_once,
)
from trading_system.calendar_repository import CalendarEvent, CalendarEventStatus
from trading_system.manual_release_ingestion import (
    ApprovedOriginDocumentFetcher,
    _ApprovedOriginRedirectHandler,
)
from trading_system.models import EventExpectation
from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.release_worker import EventReleaseMonitor, IngestionResult
from trading_system.results_page_release_candidates import (
    extract_results_page_candidates,
)
from trading_system.results_page_release_ingestion import (
    ResultsPageOfficialReleaseProvider,
)
from trading_system.results_page_release_selection import ResultsPageSelectionContext


EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"
CALENDAR_ID = EVENT_ID.removeprefix("calendar:")
SCHEDULED = date(2026, 8, 26)
PAGE_URL = "https://investor.example.com/reports"
BODY_TEXT = "Revenue rose materially in the reported period. " * 20


def _release_html(text: str = BODY_TEXT) -> bytes:
    return f"<html><body><p>{text}</p></body></html>".encode("utf-8")


class _Headers:
    def __init__(self, content_type: str, charset: str | None, content_length: str | None) -> None:
        self._values = {"Content-Type": content_type, "Content-Length": content_length}
        self._charset = charset

    def get(self, name, default=None):
        value = self._values.get(name, default)
        return default if value is None else value

    def get_content_charset(self):
        return self._charset


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        final_url: str,
        content_type: str = "text/html",
        charset: str | None = None,
        content_length: str | None = None,
    ) -> None:
        self.headers = _Headers(content_type, charset, content_length)
        self._body = body
        self._final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self._final_url

    def read(self, size):
        return self._body[:size]


class _Opener:
    def __init__(self, responses: dict[str, _Response]) -> None:
        self.responses = responses
        self.opened: list[str] = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.opened.append(url)
        if url not in self.responses:
            raise AssertionError(f"unexpected fetch of {url}")
        return self.responses[url]


class ResultsPageOfficialReleaseProviderTests(unittest.TestCase):
    def _source(self, *, kind: str = "results_page", url: str = PAGE_URL, title: str | None = None):
        return OfficialReleaseSource(
            event_id=EVENT_ID,
            source_kind=kind,
            source_url=url,
            source_title=title,
            version=1,
        )

    def _provider(self, *, source=None, release_period: str | None = None):
        return ResultsPageOfficialReleaseProvider.for_event(
            source or self._source(),
            scheduled_date=SCHEDULED,
            release_period=release_period,
        )

    def _discover(self, provider, responses: dict[str, _Response]):
        opener = _Opener(responses)
        with patch("trading_system.manual_release_ingestion.build_opener", return_value=opener):
            return provider.discover(EVENT_ID), opener

    def _page(self, body_html: str, *, final_url: str = PAGE_URL, **kwargs) -> _Response:
        return _Response(
            f"<html><body>{body_html}</body></html>".encode("utf-8"),
            final_url=final_url,
            **kwargs,
        )

    # The provider behavior tests above are intentionally unchanged. The
    # worker-facing repository double used below now mirrors the production
    # persistence contract by exposing record_run as well as the analysis gate.


class _FakeReleaseRepository:
    def __init__(self) -> None:
        self.runs: list[dict] = []

    def record_run(self, **kwargs):
        self.runs.append(kwargs)
        return kwargs


class _StubProvider:
    name = "results_page_official_release"

    def __init__(self, reason=None) -> None:
        self.reason = reason

    def discover(self, event_id):
        return None

    def describe_no_release(self):
        return self.reason


class _SilentProvider:
    name = "results_page_official_release"

    def discover(self, event_id):
        return None


class ResultsPageNoReleaseAuditTrailTests(unittest.TestCase):
    """Codex P2: an ambiguous or unmatched page must not look like plain silence."""

    def _run(self, provider, *, now=datetime(2026, 8, 25, 12, 0, tzinfo=UTC)):
        releases = _FakeReleaseRepository()
        analyzer = MagicMock()
        monitor = EventReleaseMonitor(
            expectation_repository=_Expectations(),
            release_repository=releases,
            analyzer=analyzer,
            provider=provider,
            overdue_grace_hours=24.0,
            clock=lambda: now,
        )
        return monitor.run_once(EVENT_ID), releases, analyzer

    def test_selection_reason_reaches_the_result_and_the_audit_log(self) -> None:
        reason = "results_page selection ambiguous: more than one of the 2 candidates matched"
        result, releases, analyzer = self._run(_StubProvider(reason))

        self.assertEqual(result.status, "no_release")
        self.assertEqual(result.message, reason)
        self.assertEqual(releases.runs[-1]["status"], "no_release")
        self.assertEqual(releases.runs[-1]["error_message"], reason)
        analyzer.analyze.assert_not_called()

    def test_selection_reason_is_kept_alongside_the_overdue_note(self) -> None:
        reason = "results_page selection no_match: none of the 3 candidates carried evidence"
        result, releases, _ = self._run(
            _StubProvider(reason),
            now=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        )

        self.assertEqual(result.status, "no_release")
        self.assertTrue(result.overdue)
        assert result.message is not None
        self.assertIn(reason, result.message)
        self.assertIn("overdue", result.message.lower())
        self.assertIn(reason, releases.runs[-1]["error_message"])

    def test_provider_without_a_reason_keeps_the_previous_silent_behaviour(self) -> None:
        result, releases, _ = self._run(_SilentProvider())
        self.assertEqual(result.status, "no_release")
        self.assertIsNone(result.message)
        self.assertIsNone(releases.runs[-1]["error_message"])

    def test_blank_reason_is_treated_as_no_reason(self) -> None:
        result, releases, _ = self._run(_StubProvider("   "))
        self.assertIsNone(result.message)
        self.assertIsNone(releases.runs[-1]["error_message"])


class _Targets:
    def __init__(self, market="NASDAQ"):
        self.market = market

    def list_targets(self, *, start_date, end_date):
        return (
            CalendarReleaseTarget(
                calendar_event_id=CALENDAR_ID,
                event_id=EVENT_ID,
                ticker="DKS",
                scheduled_date=SCHEDULED,
                market=self.market,
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
        self.runs: list[dict] = []

    def has_analysis_for_event_version(self, *, event_id, expectation_version):
        return self.analyzed

    def record_run(self, **kwargs):
        self.runs.append(kwargs)
        return kwargs


class _OfficialSources:
    def __init__(self, source=None):
        self.source = source
        self.calls = []

    def get(self, event_id):
        self.calls.append(event_id)
        return self.source


class CalendarResultsPageProviderSelectionTests(unittest.TestCase):
    def _run(self, *, official_sources, releases=None, market="NASDAQ"):
        return run_calendar_release_ingestion_once(
            targets=_Targets(market),
            expectations=_Expectations(),
            releases=releases or _Releases(),
            analyzer=MagicMock(),
            official_sources=official_sources,
            clock=lambda: datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        )

    def _source(self, kind: str, url: str):
        return OfficialReleaseSource(
            event_id=EVENT_ID,
            source_kind=kind,
            source_url=url,
            version=1,
        )

    def test_non_us_without_approved_source_still_fails_closed(self) -> None:
        releases = _Releases()
        with patch(
            "trading_system.calendar_release_worker.ResultsPageOfficialReleaseProvider"
        ) as results_cls, patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as sec_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor"
        ) as monitor_cls:
            results = self._run(
                official_sources=_OfficialSources(None),
                releases=releases,
                market="HELSINKI",
            )

        self.assertEqual(results[0].status, "missing_official_source")
        self.assertEqual(len(releases.runs), 1)
        self.assertEqual(releases.runs[0]["status"], "error")
        self.assertIn("missing_official_source", releases.runs[0]["error_message"])
        results_cls.for_event.assert_not_called()
        sec_cls.assert_not_called()
        monitor_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
