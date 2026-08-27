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

    # 1 / 15 / 16 -------------------------------------------------------
    def test_exact_scheduled_date_selects_unique_html_release(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-half-year-results.html"
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<a href="/reports/2026-08-26-half-year-results.html">Half-year results</a>'
                    '<a href="/reports/2025-08-26-half-year-results.html">Half-year results 2025</a>'
                ),
                document_url: _Response(_release_html(), final_url=document_url),
            },
        )

        self.assertIsNotNone(document)
        self.assertEqual(document.event_id, EVENT_ID)
        self.assertEqual(document.source_type, "company_results")
        # The persisted release is the selected document, never the results page.
        self.assertEqual(document.source_url, document_url)
        self.assertEqual(document.source_title, "Half-year results")
        self.assertIn("Revenue rose materially", document.raw_text)
        self.assertEqual(opener.opened, [PAGE_URL, document_url])

    # 2 ----------------------------------------------------------------
    def test_exact_scheduled_date_selects_pdf_release(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-results.pdf"
        provider = self._provider()
        with patch.object(
            ResultsPageOfficialReleaseProvider,
            "_extract_pdf_text",
            return_value=BODY_TEXT,
        ) as extract:
            document, _ = self._discover(
                provider,
                {
                    PAGE_URL: self._page(
                        '<a href="/reports/2026-08-26-results.pdf">Interim report</a>'
                    ),
                    document_url: _Response(
                        b"%PDF-1.7 binary",
                        final_url=document_url,
                        content_type="application/pdf",
                    ),
                },
            )

        extract.assert_called_once_with(b"%PDF-1.7 binary")
        self.assertEqual(document.source_type, "company_results_pdf")
        self.assertEqual(document.source_url, document_url)
        self.assertEqual(document.source_title, "Interim report")

    # 3 ----------------------------------------------------------------
    def test_no_matching_candidate_returns_none_without_fetching_a_document(self) -> None:
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<a href="/reports/2025-08-26-results.html">Older results</a>'
                    '<a href="/reports/annual-report.html">Annual report</a>'
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])

    def test_page_without_candidates_returns_none(self) -> None:
        provider = self._provider()
        document, opener = self._discover(provider, {PAGE_URL: self._page("<p>No links here</p>")})

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])

    def test_base_identity_failure_is_auditable_not_no_candidates(self) -> None:
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<table><base href="/downloads/"></table>'
                    '<a href="2026-08-26-results.html">Results</a>'
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])
        reason = provider.describe_no_release()
        self.assertIsNotNone(reason)
        self.assertIn("base_identity_failed", reason)
        self.assertNotIn("selection no_candidates", reason)

    def test_reserved_base_marker_failure_is_auditable(self) -> None:
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<base data-mmb-base-token-spoof="0" href="/downloads/">'
                    '<a href="2026-08-26-results.html">Results</a>'
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])
        reason = provider.describe_no_release()
        self.assertIsNotNone(reason)
        self.assertIn("base_identity_failed", reason)


    def test_cross_origin_base_failure_is_auditable(self) -> None:
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<base href="https://cdn.example.net/releases/">'
                    '<a href="2026-08-26-results.html">Results</a>'
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])
        reason = provider.describe_no_release()
        self.assertIsNotNone(reason)
        self.assertIn("base_resolution_failed", reason)
        self.assertNotIn("selection no_candidates", reason)

    def test_http_base_failure_is_auditable(self) -> None:
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<base href="http://investor.example.com/releases/">'
                    '<a href="2026-08-26-results.html">Results</a>'
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])
        reason = provider.describe_no_release()
        self.assertIsNotNone(reason)
        self.assertIn("base_resolution_failed", reason)

    # 4 ----------------------------------------------------------------
    def test_ambiguous_candidates_return_none_without_fetching_a_document(self) -> None:
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<a href="/reports/2026-08-26-results.html">Results release</a>'
                    '<a href="/reports/2026-08-26-presentation.html">Results presentation</a>'
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])

    # 5 ----------------------------------------------------------------
    def test_explicit_release_period_selects_unique_release(self) -> None:
        document_url = "https://investor.example.com/reports/q3-2026-results.html"
        provider = self._provider(release_period="Q3 2026")
        document, _ = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<a href="/reports/q3-2026-results.html">Third quarter results</a>'
                    '<a href="/reports/q2-2026-results.html">Second quarter results</a>'
                ),
                document_url: _Response(_release_html(), final_url=document_url),
            },
        )

        self.assertIsNotNone(document)
        self.assertEqual(document.source_url, document_url)

    # 6 ----------------------------------------------------------------
    def test_period_is_never_inferred_from_the_scheduled_date(self) -> None:
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<a href="/reports/q3-2026-results.html">Third quarter results</a>'
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])

    # 7 ----------------------------------------------------------------
    def test_event_id_mismatch_fails_closed(self) -> None:
        provider = self._provider()
        with self.assertRaisesRegex(ValueError, "event_id mismatch"):
            provider.discover("calendar:some-other-event")

    # 8 ----------------------------------------------------------------
    def test_direct_url_source_is_rejected_by_the_constructor(self) -> None:
        source = self._source(kind="direct_url", url="https://investor.example.com/results.pdf")
        with self.assertRaisesRegex(ValueError, "requires source_kind=results_page"):
            ResultsPageOfficialReleaseProvider.for_event(source, scheduled_date=SCHEDULED)

    def test_selection_context_for_another_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "selection context event identity mismatch"):
            ResultsPageOfficialReleaseProvider(
                self._source(),
                ResultsPageSelectionContext(
                    calendar_event_id="calendar:another-event",
                    scheduled_date=SCHEDULED,
                ),
            )

    def test_calendar_event_is_accepted_as_selection_context(self) -> None:
        event = CalendarEvent(
            calendar_event_id=EVENT_ID,
            company_name="Example Oyj",
            instrument="EXAMPLE.HE",
            market="Helsinki",
            event_type="earnings",
            scheduled_date=SCHEDULED,
            source="finnhub",
            occurrence_key="2026Q3",
            status=CalendarEventStatus.TRACKED,
        )
        provider = ResultsPageOfficialReleaseProvider(self._source(), event)
        self.assertIs(provider.selection_context, event)

    # 9 ----------------------------------------------------------------
    def test_results_page_redirect_off_the_approved_origin_is_rejected(self) -> None:
        provider = self._provider()
        with self.assertRaisesRegex(RuntimeError, "left approved HTTPS origin"):
            self._discover(
                provider,
                {
                    PAGE_URL: self._page(
                        '<a href="/reports/2026-08-26-results.html">Results</a>',
                        final_url="https://cdn.example.net/reports",
                    )
                },
            )

    def test_results_page_redirect_is_rejected_before_being_followed(self) -> None:
        handler = _ApprovedOriginRedirectHandler(PAGE_URL)
        for final_url in (
            "https://cdn.example.net/reports",
            "http://investor.example.com/reports",
            "https://investor.example.com:8443/reports",
        ):
            with self.subTest(final_url=final_url):
                with self.assertRaisesRegex(RuntimeError, "left approved HTTPS origin"):
                    handler.redirect_request(
                        MagicMock(), MagicMock(), 302, "Found", MagicMock(), final_url
                    )

    # 10 ---------------------------------------------------------------
    def test_selected_document_redirect_off_the_approved_origin_is_rejected(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-results.html"
        provider = self._provider()
        with self.assertRaisesRegex(RuntimeError, "left approved HTTPS origin"):
            self._discover(
                provider,
                {
                    PAGE_URL: self._page(
                        '<a href="/reports/2026-08-26-results.html">Results</a>'
                    ),
                    document_url: _Response(
                        _release_html(),
                        final_url="https://cdn.example.net/reports/2026-08-26-results.html",
                    ),
                },
            )

    # 11 ---------------------------------------------------------------
    def test_cross_origin_links_never_become_candidates(self) -> None:
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<a href="https://cdn.example.net/reports/2026-08-26-results.html">Results</a>'
                    '<a href="http://investor.example.com/reports/2026-08-26-results.html">Results</a>'
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])

    # 12 ---------------------------------------------------------------
    def test_selected_document_that_is_too_short_returns_none(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-results.html"
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page('<a href="/reports/2026-08-26-results.html">Results</a>'),
                document_url: _Response(_release_html("Results published."), final_url=document_url),
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL, document_url])

    # 13 ---------------------------------------------------------------
    def test_results_page_is_decoded_with_its_declared_charset(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-results.html"
        page = (
            '<html><head><meta charset="ISO-8859-1"></head><body>'
            '<a href="/reports/2026-08-26-results.html">Tilinpäätöstiedote</a>'
            "</body></html>"
        ).encode("iso-8859-1")
        provider = self._provider()
        document, _ = self._discover(
            provider,
            {
                PAGE_URL: _Response(page, final_url=PAGE_URL),
                document_url: _Response(_release_html(), final_url=document_url),
            },
        )

        self.assertEqual(document.source_title, "Tilinpäätöstiedote")

    def test_results_page_that_is_not_markup_fails_closed(self) -> None:
        provider = self._provider()
        for content_type, body in (
            ("application/pdf", b"%PDF-1.7 binary"),
            ("text/plain", b"Results are published quarterly."),
            ("", b"Results are published quarterly."),
        ):
            with self.subTest(content_type=content_type):
                with self.assertRaisesRegex(RuntimeError, "unsupported content type"):
                    self._discover(
                        provider,
                        {
                            PAGE_URL: _Response(
                                body, final_url=PAGE_URL, content_type=content_type
                            )
                        },
                    )

    # 14 ---------------------------------------------------------------
    def test_pdf_extraction_reuses_the_approved_source_resource_limits(self) -> None:
        for name in (
            "MAX_DOWNLOAD_BYTES",
            "MAX_PDF_PAGES",
            "MAX_EXTRACTED_CHARS",
            "PDF_EXTRACTION_MEMORY_BYTES",
            "PDF_EXTRACTION_CPU_SECONDS",
            "PDF_EXTRACTION_TIMEOUT_SECONDS",
            "MIN_DOCUMENT_CHARS",
        ):
            with self.subTest(limit=name):
                self.assertEqual(
                    getattr(ResultsPageOfficialReleaseProvider, name),
                    getattr(ApprovedOriginDocumentFetcher, name),
                )

        reader = MagicMock()
        reader.pages = [MagicMock()] * (ApprovedOriginDocumentFetcher.MAX_PDF_PAGES + 1)
        with patch("trading_system.manual_release_ingestion.PdfReader", return_value=reader):
            with self.assertRaisesRegex(RuntimeError, "exceeds page limit"):
                ResultsPageOfficialReleaseProvider._extract_pdf_text_in_process(b"%PDF-1.7")

    def test_approved_source_title_is_the_fallback_when_the_link_has_no_text(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-results.html"
        provider = self._provider(source=self._source(title="Approved results page"))
        document, _ = self._discover(
            provider,
            {
                PAGE_URL: self._page('<a href="/reports/2026-08-26-results.html"></a>'),
                document_url: _Response(_release_html(), final_url=document_url),
            },
        )

        self.assertEqual(document.source_title, "Approved results page")

    # Codex P1: relative links resolve against the URL the page was served from
    def test_relative_links_resolve_against_a_same_origin_redirect_target(self) -> None:
        served_page_url = "https://investor.example.com/investors/reports/"
        document_url = "https://investor.example.com/investors/reports/2026-08-26-results.html"
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<a href="2026-08-26-results.html">Half-year results</a>',
                    final_url=served_page_url,
                ),
                document_url: _Response(_release_html(), final_url=document_url),
            },
        )

        # Resolved against the approved URL instead, this would have been
        # https://investor.example.com/2026-08-26-results.html - a different
        # same-origin document.
        self.assertEqual(document.source_url, document_url)
        self.assertEqual(opener.opened, [PAGE_URL, document_url])

    def test_relative_links_resolve_against_the_approved_url_without_a_redirect(self) -> None:
        document_url = "https://investor.example.com/2026-08-26-results.html"
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page('<a href="2026-08-26-results.html">Half-year results</a>'),
                document_url: _Response(_release_html(), final_url=document_url),
            },
        )

        self.assertEqual(document.source_url, document_url)
        self.assertEqual(opener.opened, [PAGE_URL, document_url])

    def test_redirect_target_does_not_widen_the_approved_origin(self) -> None:
        served_page_url = "https://investor.example.com/investors/reports/"
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<a href="https://cdn.example.net/investors/reports/2026-08-26-results.html">Results</a>'
                    '<a href="//cdn.example.net/2026-08-26-results.html">Results</a>',
                    final_url=served_page_url,
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, [PAGE_URL])

    def test_link_back_to_the_pre_redirect_results_page_is_not_a_candidate(self) -> None:
        served_page_url = "https://investor.example.com/investors/reports/"
        provider = self._provider(source=self._source(url="https://investor.example.com/2026-08-26"))
        document, opener = self._discover(
            provider,
            {
                "https://investor.example.com/2026-08-26": self._page(
                    '<a href="/2026-08-26">Results home</a>',
                    final_url=served_page_url,
                )
            },
        )

        self.assertIsNone(document)
        self.assertEqual(opener.opened, ["https://investor.example.com/2026-08-26"])

    def test_extractor_rejects_a_page_url_off_the_approved_origin(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved HTTPS origin"):
            extract_results_page_candidates(
                self._source(),
                '<html><body><a href="/reports/2026-08-26-results.html">Results</a></body></html>',
                page_url="https://cdn.example.net/reports/",
            )

    # Codex P2 (round 2): a document <base href> repoints relative links
    def _candidates(self, body_html: str, *, page_url: str | None = None):
        return extract_results_page_candidates(
            self._source(),
            f"<html><head></head><body>{body_html}</body></html>",
            page_url=page_url,
        )

    def _candidate_urls(self, body_html: str, *, page_url: str | None = None):
        return tuple(c.source_url for c in self._candidates(body_html, page_url=page_url))

    def test_relative_base_href_repoints_relative_links(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<base href="/downloads/"><a href="2026-08-26-results.pdf">Q2 2026</a>'
            ),
            ("https://investor.example.com/downloads/2026-08-26-results.pdf",),
        )

    def test_base_href_in_head_repoints_relative_links(self) -> None:
        candidates = extract_results_page_candidates(
            self._source(),
            '<html><head><base href="/downloads/"></head><body>'
            '<a href="2026-08-26-results.pdf">Q2 2026</a></body></html>',
        )
        self.assertEqual(
            tuple(c.source_url for c in candidates),
            ("https://investor.example.com/downloads/2026-08-26-results.pdf",),
        )

    def test_absolute_same_origin_base_href_is_accepted(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<base href="https://investor.example.com/downloads/">'
                '<a href="2026-08-26-results.pdf">Q2 2026</a>'
            ),
            ("https://investor.example.com/downloads/2026-08-26-results.pdf",),
        )

    def test_base_href_dot_segments_are_canonicalised(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<base href="/investors/reports/../../downloads/#anchor">'
                '<a href="2026-08-26-results.pdf">Q2 2026</a>'
            ),
            ("https://investor.example.com/downloads/2026-08-26-results.pdf",),
        )

    def test_first_base_with_an_href_wins(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<base href="/first/"><base href="/second/">'
                '<a href="2026-08-26-results.pdf">Q2 2026</a>'
            ),
            ("https://investor.example.com/first/2026-08-26-results.pdf",),
        )

    def test_base_without_an_href_does_not_set_the_base(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<base target="_blank"><base href="/downloads/">'
                '<a href="2026-08-26-results.pdf">Q2 2026</a>'
            ),
            ("https://investor.example.com/downloads/2026-08-26-results.pdf",),
        )

    def test_unusable_base_href_fails_the_whole_page_closed(self) -> None:
        for base in (
            "https://cdn.example.net/downloads/",
            "http://investor.example.com/downloads/",
            "https://user:pass@investor.example.com/downloads/",
            "https://investor.example.com:99999/downloads/",
            "https://investor.example.com:8443/downloads/",
            "https://-invalid-.example.com/downloads/",
            "javascript:void(0)",
        ):
            with self.subTest(base=base):
                self.assertEqual(
                    self._candidate_urls(
                        f'<base href="{base}"><a href="/reports/2026-08-26-results.html">Q2</a>'
                    ),
                    (),
                )

    def test_base_href_never_silently_falls_back_to_the_response_url(self) -> None:
        # Without the fail-closed rule this page would still yield the anchor
        # resolved against the response URL.
        self.assertEqual(
            self._candidate_urls('<a href="/reports/2026-08-26-results.html">Q2</a>'),
            ("https://investor.example.com/reports/2026-08-26-results.html",),
        )
        self.assertEqual(
            self._candidate_urls(
                '<base href="http://investor.example.com/downloads/">'
                '<a href="/reports/2026-08-26-results.html">Q2</a>'
            ),
            (),
        )

    def test_absolute_candidate_hrefs_ignore_the_base(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<base href="/downloads/">'
                '<a href="https://investor.example.com/reports/2026-08-26-results.html">Q2</a>'
                '<a href="/reports/2026-08-27-results.html">Q3</a>'
            ),
            (
                "https://investor.example.com/reports/2026-08-26-results.html",
                "https://investor.example.com/reports/2026-08-27-results.html",
            ),
        )

    def test_base_href_resolves_against_the_redirected_results_page_url(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<base href="downloads/"><a href="2026-08-26-results.pdf">Q2 2026</a>',
                page_url="https://investor.example.com/investors/results/",
            ),
            ("https://investor.example.com/investors/results/downloads/2026-08-26-results.pdf",),
        )

    def test_self_page_exclusion_applies_to_base_resolved_candidates(self) -> None:
        served_page_url = "https://investor.example.com/investors/results/"
        body = (
            '<base href="/investors/results/">'
            '<a href="">This page</a>'
            '<a href="../../reports">Approved page</a>'
            '<a href="2026-08-26-results.pdf">Q2 2026</a>'
        )
        self.assertEqual(
            self._candidate_urls(body, page_url=served_page_url),
            ("https://investor.example.com/investors/results/2026-08-26-results.pdf",),
        )

    def test_base_href_reaches_the_provider_end_to_end(self) -> None:
        document_url = "https://investor.example.com/downloads/2026-08-26-results.pdf"
        provider = self._provider()
        with patch.object(
            ResultsPageOfficialReleaseProvider, "_extract_pdf_text", return_value=BODY_TEXT
        ):
            document, opener = self._discover(
                provider,
                {
                    PAGE_URL: self._page(
                        '<base href="/downloads/"><a href="2026-08-26-results.pdf">Q2 2026</a>'
                    ),
                    document_url: _Response(
                        b"%PDF-1.7 binary", final_url=document_url, content_type="application/pdf"
                    ),
                },
            )

        self.assertEqual(document.source_url, document_url)
        self.assertEqual(opener.opened, [PAGE_URL, document_url])

    def test_template_local_base_never_sets_the_document_base(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<template><base href="/injected/"></template><base href="/downloads/">'
                '<a href="2026-08-26-results.pdf">Q2 2026</a>'
            ),
            ("https://investor.example.com/downloads/2026-08-26-results.pdf",),
        )

    def test_foreign_base_never_sets_the_document_base(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<svg><base href="/injected/"></svg><base href="/downloads/">'
                '<a href="2026-08-26-results.pdf">Q2 2026</a>'
            ),
            ("https://investor.example.com/downloads/2026-08-26-results.pdf",),
        )

    def test_base_href_smuggling_a_control_character_fails_closed(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<base href="/down&#10;loads/"><a href="2026-08-26-results.pdf">Q2</a>'
            ),
            (),
        )

    def test_duplicate_href_on_the_effective_base_fails_closed(self) -> None:
        self.assertEqual(
            self._candidate_urls(
                '<base href="/downloads/" href="/other/">'
                '<a href="2026-08-26-results.pdf">Q2</a>'
            ),
            (),
        )

    # Codex P1 (round 2): a candidate that redirects back to the results page
    # is the listing page, not a release - whatever its size.
    def test_selected_candidate_redirected_to_the_served_results_page_is_rejected(self) -> None:
        served_page_url = "https://investor.example.com/investors/results/"
        candidate_url = "https://investor.example.com/releases/q2-2026-08-26.html"
        page_markup = (
            f'<a href="{candidate_url}">Half-year results</a><p>{BODY_TEXT}</p>'
        )
        provider = self._provider()
        with patch.object(
            ResultsPageOfficialReleaseProvider, "_interpret_document"
        ) as interpret:
            document, opener = self._discover(
                provider,
                {
                    PAGE_URL: self._page(page_markup, final_url=served_page_url),
                    candidate_url: self._page(page_markup, final_url=served_page_url),
                },
            )

        self.assertIsNone(document)
        # The results page is long enough to pass MIN_DOCUMENT_CHARS, so only
        # the URL check keeps it out of the release store.
        self.assertGreater(
            len(BODY_TEXT), ResultsPageOfficialReleaseProvider.MIN_DOCUMENT_CHARS
        )
        interpret.assert_not_called()
        self.assertEqual(opener.opened, [PAGE_URL, candidate_url])

    def test_selected_candidate_redirected_to_the_approved_results_page_is_rejected(self) -> None:
        served_page_url = "https://investor.example.com/investors/results/"
        candidate_url = "https://investor.example.com/releases/q2-2026-08-26.html"
        page_markup = f'<a href="{candidate_url}">Half-year results</a><p>{BODY_TEXT}</p>'
        provider = self._provider()
        with patch.object(
            ResultsPageOfficialReleaseProvider, "_interpret_document"
        ) as interpret:
            document, _ = self._discover(
                provider,
                {
                    PAGE_URL: self._page(page_markup, final_url=served_page_url),
                    # Redirected back to the pre-redirect approved page instead.
                    candidate_url: self._page(page_markup, final_url=PAGE_URL),
                },
            )

        self.assertIsNone(document)
        interpret.assert_not_called()

    def test_results_page_self_redirect_is_reported_as_an_auditable_reason(self) -> None:
        served_page_url = "https://investor.example.com/investors/results/"
        candidate_url = "https://investor.example.com/releases/q2-2026-08-26.html"
        page_markup = f'<a href="{candidate_url}">Half-year results</a><p>{BODY_TEXT}</p>'
        provider = self._provider()
        self._discover(
            provider,
            {
                PAGE_URL: self._page(page_markup, final_url=served_page_url),
                candidate_url: self._page(page_markup, final_url=served_page_url),
            },
        )

        reason = provider.describe_no_release()
        assert reason is not None
        self.assertIn("redirected to the results page", reason)
        self.assertIn(candidate_url, reason)
        self.assertIn(served_page_url, reason)

    def test_same_origin_redirect_to_a_different_document_is_accepted(self) -> None:
        candidate_url = "https://investor.example.com/reports/2026-08-26-results.html"
        served_document_url = "https://investor.example.com/reports/final/2026-08-26-results.html"
        provider = self._provider()
        document, opener = self._discover(
            provider,
            {
                PAGE_URL: self._page(f'<a href="{candidate_url}">Half-year results</a>'),
                candidate_url: _Response(_release_html(), final_url=served_document_url),
            },
        )

        self.assertIsNotNone(document)
        # The contract for this provider is the URL the document was served
        # from, not the link that pointed at it.
        self.assertEqual(document.source_url, served_document_url)
        self.assertEqual(document.source_title, "Half-year results")
        self.assertEqual(opener.opened, [PAGE_URL, candidate_url])

    def test_selected_document_source_url_is_canonicalised(self) -> None:
        candidate_url = "https://investor.example.com/reports/2026-08-26-results.html"
        provider = self._provider()
        document, _ = self._discover(
            provider,
            {
                PAGE_URL: self._page(f'<a href="{candidate_url}">Results</a>'),
                candidate_url: _Response(
                    _release_html(),
                    final_url="https://INVESTOR.example.com:443/reports/./final/../2026-08-26-results.html#top",
                ),
            },
        )

        self.assertEqual(document.source_url, candidate_url)

    # Codex P2: a fail-closed outcome always says why
    def test_no_release_reason_is_empty_before_and_after_a_successful_discovery(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-results.html"
        provider = self._provider()
        self.assertIsNone(provider.describe_no_release())
        self._discover(
            provider,
            {
                PAGE_URL: self._page('<a href="/reports/2026-08-26-results.html">Results</a>'),
                document_url: _Response(_release_html(), final_url=document_url),
            },
        )
        self.assertIsNone(provider.describe_no_release())

    def test_ambiguous_selection_is_reported_as_an_auditable_reason(self) -> None:
        provider = self._provider()
        self._discover(
            provider,
            {
                PAGE_URL: self._page(
                    '<a href="/reports/2026-08-26-results.html">Results release</a>'
                    '<a href="/reports/2026-08-26-presentation.html">Results presentation</a>'
                )
            },
        )

        reason = provider.describe_no_release()
        assert reason is not None
        self.assertIn("ambiguous", reason)
        self.assertIn("2026-08-26", reason)
        self.assertIn(PAGE_URL, reason)

    def test_no_match_selection_is_reported_as_an_auditable_reason(self) -> None:
        provider = self._provider()
        self._discover(
            provider,
            {PAGE_URL: self._page('<a href="/reports/2025-08-26-results.html">Older results</a>')},
        )

        reason = provider.describe_no_release()
        assert reason is not None
        self.assertIn("no_match", reason)
        self.assertIn("scheduled_date=2026-08-26", reason)
        self.assertIn("release_period=<none>", reason)

    def test_page_without_candidates_is_reported_as_an_auditable_reason(self) -> None:
        provider = self._provider()
        self._discover(provider, {PAGE_URL: self._page("<p>No links here</p>")})

        reason = provider.describe_no_release()
        assert reason is not None
        self.assertIn("no_candidates", reason)

    def test_too_short_document_is_reported_as_an_auditable_reason(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-results.html"
        provider = self._provider()
        self._discover(
            provider,
            {
                PAGE_URL: self._page('<a href="/reports/2026-08-26-results.html">Results</a>'),
                document_url: _Response(_release_html("Results published."), final_url=document_url),
            },
        )

        reason = provider.describe_no_release()
        assert reason is not None
        self.assertIn("too short", reason)
        self.assertIn(document_url, reason)

    def test_a_stale_reason_never_survives_into_the_next_discovery(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-results.html"
        provider = self._provider()
        self._discover(provider, {PAGE_URL: self._page("<p>No links here</p>")})
        self.assertIsNotNone(provider.describe_no_release())

        self._discover(
            provider,
            {
                PAGE_URL: self._page('<a href="/reports/2026-08-26-results.html">Results</a>'),
                document_url: _Response(_release_html(), final_url=document_url),
            },
        )
        self.assertIsNone(provider.describe_no_release())

    def test_untitled_link_without_approved_title_uses_an_explicit_fallback(self) -> None:
        document_url = "https://investor.example.com/reports/2026-08-26-results.html"
        provider = self._provider()
        document, _ = self._discover(
            provider,
            {
                PAGE_URL: self._page('<a href="/reports/2026-08-26-results.html"></a>'),
                document_url: _Response(_release_html(), final_url=document_url),
            },
        )

        self.assertEqual(document.source_title, "results-page-official-release")


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

        # The shared status semantics are unchanged; the explanation rides along.
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

    def test_worker_surfaces_the_selection_reason_for_the_event(self) -> None:
        source = OfficialReleaseSource(
            event_id=EVENT_ID,
            source_kind="results_page",
            source_url=PAGE_URL,
            version=1,
        )
        reason = "results_page selection ambiguous: more than one of the 2 candidates matched"
        provider = _StubProvider(reason)
        releases = _FakeReleaseRepository()
        releases.has_analysis_for_event_version = lambda **kwargs: False

        with patch(
            "trading_system.calendar_release_worker.ResultsPageOfficialReleaseProvider"
        ) as results_cls:
            results_cls.for_event.return_value = provider
            results = run_calendar_release_ingestion_once(
                targets=_Targets(),
                expectations=_Expectations(),
                releases=releases,
                analyzer=MagicMock(),
                official_sources=_OfficialSources(source),
                clock=lambda: datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
            )

        self.assertEqual(results[0].status, "no_release")
        assert results[0].message is not None
        self.assertIn("ambiguous", results[0].message)


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

    def has_analysis_for_event_version(self, *, event_id, expectation_version):
        return self.analyzed


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

    # 17 ---------------------------------------------------------------
    def test_direct_url_source_still_selects_the_manual_provider(self) -> None:
        source = self._source("direct_url", "https://investor.example.com/results.pdf")
        fake_monitor = MagicMock()
        fake_monitor.run_once.return_value = IngestionResult(status="no_release")
        manual_provider = MagicMock()

        with patch(
            "trading_system.calendar_release_worker.ManualOfficialReleaseProvider",
            return_value=manual_provider,
        ) as manual_cls, patch(
            "trading_system.calendar_release_worker.ResultsPageOfficialReleaseProvider"
        ) as results_cls, patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as sec_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            return_value=fake_monitor,
        ) as monitor_cls:
            results = self._run(official_sources=_OfficialSources(source))

        self.assertEqual(results[0].status, "no_release")
        manual_cls.assert_called_once_with(source)
        results_cls.for_event.assert_not_called()
        sec_cls.assert_not_called()
        self.assertIs(monitor_cls.call_args.kwargs["provider"], manual_provider)

    # 18 ---------------------------------------------------------------
    def test_results_page_source_selects_the_results_page_provider(self) -> None:
        source = self._source("results_page", PAGE_URL)
        fake_monitor = MagicMock()
        fake_monitor.run_once.return_value = IngestionResult(status="no_release")
        results_provider = MagicMock()

        with patch(
            "trading_system.calendar_release_worker.ManualOfficialReleaseProvider"
        ) as manual_cls, patch(
            "trading_system.calendar_release_worker.ResultsPageOfficialReleaseProvider"
        ) as results_cls, patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as sec_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            return_value=fake_monitor,
        ) as monitor_cls:
            results_cls.for_event.return_value = results_provider
            results = self._run(official_sources=_OfficialSources(source))

        self.assertEqual(results[0].status, "no_release")
        manual_cls.assert_not_called()
        sec_cls.assert_not_called()
        results_cls.for_event.assert_called_once_with(source, scheduled_date=SCHEDULED)
        self.assertIs(monitor_cls.call_args.kwargs["provider"], results_provider)
        fake_monitor.run_once.assert_called_once_with(EVENT_ID)

    def test_results_page_source_is_used_for_non_us_markets_too(self) -> None:
        source = self._source("results_page", PAGE_URL)
        fake_monitor = MagicMock()
        fake_monitor.run_once.return_value = IngestionResult(status="no_release")

        with patch(
            "trading_system.calendar_release_worker.ResultsPageOfficialReleaseProvider"
        ) as results_cls, patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as sec_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            return_value=fake_monitor,
        ):
            results = self._run(official_sources=_OfficialSources(source), market="HELSINKI")

        self.assertEqual(results[0].status, "no_release")
        results_cls.for_event.assert_called_once_with(source, scheduled_date=SCHEDULED)
        sec_cls.assert_not_called()

    # 19 ---------------------------------------------------------------
    def test_us_without_approved_source_keeps_the_sec_fallback(self) -> None:
        fake_monitor = MagicMock()
        fake_monitor.run_once.return_value = IngestionResult(status="no_release")
        sec_provider = MagicMock()

        with patch(
            "trading_system.calendar_release_worker.ResultsPageOfficialReleaseProvider"
        ) as results_cls, patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider",
            return_value=sec_provider,
        ) as sec_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor",
            return_value=fake_monitor,
        ) as monitor_cls:
            results = self._run(official_sources=_OfficialSources(None))

        self.assertEqual(results[0].status, "no_release")
        results_cls.for_event.assert_not_called()
        sec_cls.assert_called_once_with(ticker="DKS", scheduled_date=SCHEDULED)
        self.assertIs(monitor_cls.call_args.kwargs["provider"], sec_provider)

    # 20 ---------------------------------------------------------------
    def test_non_us_without_approved_source_still_fails_closed(self) -> None:
        with patch(
            "trading_system.calendar_release_worker.ResultsPageOfficialReleaseProvider"
        ) as results_cls, patch(
            "trading_system.calendar_release_worker.SecEdgarResultsProvider"
        ) as sec_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor"
        ) as monitor_cls:
            results = self._run(official_sources=_OfficialSources(None), market="HELSINKI")

        self.assertEqual(results[0].status, "missing_official_source")
        results_cls.for_event.assert_not_called()
        sec_cls.assert_not_called()
        monitor_cls.assert_not_called()

    # 21 ---------------------------------------------------------------
    def test_already_analyzed_event_skips_the_results_page_fetch(self) -> None:
        official_sources = _OfficialSources(self._source("results_page", PAGE_URL))

        with patch(
            "trading_system.calendar_release_worker.ResultsPageOfficialReleaseProvider"
        ) as results_cls, patch(
            "trading_system.calendar_release_worker.EventReleaseMonitor"
        ) as monitor_cls, patch(
            "trading_system.manual_release_ingestion.build_opener"
        ) as build_opener:
            results = self._run(
                official_sources=official_sources,
                releases=_Releases(analyzed=True),
            )

        self.assertEqual(results[0].status, "already_analyzed")
        self.assertEqual(official_sources.calls, [])
        results_cls.for_event.assert_not_called()
        monitor_cls.assert_not_called()
        build_opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
