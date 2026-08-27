from __future__ import annotations

from datetime import date

from trading_system.manual_release_ingestion import ApprovedOriginDocumentFetcher
from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.release_ingestion import ReleaseDocument
from trading_system.results_page_release_candidates import extract_results_page_candidates
from trading_system.results_page_release_selection import (
    ResultsPageSelectionContext,
    ResultsPageSelectionStatus,
    ResultsPageSelectionTarget,
    select_results_page_release_candidate,
)


class ResultsPageOfficialReleaseProvider(ApprovedOriginDocumentFetcher):
    """Pick exactly one release document off a user-approved results page.

    This is the runtime bridge between the already-reviewed results-page
    candidate extraction and the existing ``EventReleaseMonitor`` path. It adds
    no discovery of its own: the approved ``results_page`` URL defines the only
    origin it will ever touch, the extractor keeps every candidate same-origin
    HTTPS, and the selection stays fail-closed - an ambiguous page or a page
    with no dated/period-labelled release yields ``None`` so the monitor records
    its ordinary ``no_release`` outcome instead of guessing a link.

    Two distinct fetches happen here, both confined to the approved origin: the
    results page itself, and - only after a unique candidate has been selected -
    the release document. The results page is never persisted as a release.
    """

    name = "results_page_official_release"

    def __init__(
        self,
        source: OfficialReleaseSource,
        selection_context: ResultsPageSelectionTarget,
        *,
        release_period: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if source.source_kind != "results_page":
            raise ValueError(
                "results page official release provider requires source_kind=results_page"
            )
        if selection_context.calendar_event_id != source.event_id:
            # The extractor stamps ``source.event_id`` onto every candidate and
            # the selection matches candidates against this identity. A context
            # built for another event could only ever fail closed, silently, so
            # reject the mismatch where it is still diagnosable.
            raise ValueError(
                "results page official release selection context event identity mismatch"
            )
        super().__init__(source, timeout_seconds=timeout_seconds)
        self.selection_context = selection_context
        self.release_period = release_period

    @classmethod
    def for_event(
        cls,
        source: OfficialReleaseSource,
        *,
        scheduled_date: date,
        release_period: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> "ResultsPageOfficialReleaseProvider":
        """Build a provider from the canonical facts a release target carries.

        Callers that hold a full ``CalendarEvent`` can pass it straight to the
        constructor. Callers that legitimately hold only the event identity and
        its scheduled date use this instead of inventing the remaining canonical
        calendar fields.
        """
        return cls(
            source,
            ResultsPageSelectionContext(
                calendar_event_id=source.event_id,
                scheduled_date=scheduled_date,
            ),
            release_period=release_period,
            timeout_seconds=timeout_seconds,
        )

    def discover(self, event_id: str) -> ReleaseDocument | None:
        if event_id != self.source.event_id:
            raise ValueError("results page official release source event_id mismatch")
        if self.source.source_kind != "results_page":
            return None

        html_text = self._fetch_results_page_html()
        candidates = extract_results_page_candidates(self.source, html_text)
        if not candidates:
            return None

        selection = select_results_page_release_candidate(
            self.selection_context,
            candidates,
            release_period=self.release_period,
        )
        if selection.status is not ResultsPageSelectionStatus.SELECTED or selection.candidate is None:
            return None
        candidate = selection.candidate

        # Defence in depth. The extractor already refuses any candidate that
        # leaves the approved origin; re-checking here means a future change to
        # candidate construction cannot quietly widen what this provider will
        # download.
        self._validate_final_url(self.source.source_url, candidate.source_url)

        data, content_type, http_charset = self._fetch_bytes(candidate.source_url)
        raw_text, source_type = self._interpret_document(
            candidate.source_url,
            data,
            content_type,
            http_charset,
        )

        raw_text = raw_text.strip()
        if len(raw_text) < self.MIN_DOCUMENT_CHARS:
            return None

        return ReleaseDocument(
            event_id=event_id,
            source_type=source_type,
            source_url=candidate.source_url,
            source_title=self._document_title(candidate.source_title),
            raw_text=raw_text,
        )

    def _document_title(self, candidate_title: str | None) -> str:
        """Prefer the link text the page itself gave the selected release."""
        for value in (candidate_title, self.source.source_title):
            cleaned = (value or "").strip()
            if cleaned:
                return cleaned
        return "results-page-official-release"

    def _fetch_results_page_html(self) -> str:
        """Download the approved results page as HTML, decoded by its charset.

        The raw markup is what the candidate extractor needs, so this
        deliberately does not run the visible-text extraction the release
        document path uses. A results page that is not markup - a PDF, a plain
        text body, anything the HTML sniffing does not recognise - fails closed
        rather than being handed to the extractor.
        """
        data, content_type, http_charset = self._fetch_bytes(self.source.source_url)
        media_type = self._media_type(content_type)
        if media_type not in {"", "text/html", "application/xhtml+xml"} or not self._looks_like_html_or_text(
            content_type, data
        ):
            raise RuntimeError(
                f"results page official release returned unsupported content type: {media_type or '<missing>'}"
            )
        return self._decode_text(
            data,
            http_charset,
            allow_html_meta=True,
            allow_xml_declaration=media_type == "application/xhtml+xml",
        )
