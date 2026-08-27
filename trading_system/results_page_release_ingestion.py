from __future__ import annotations

from datetime import date

from trading_system.manual_release_ingestion import ApprovedOriginDocumentFetcher
from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.release_ingestion import ReleaseDocument
from trading_system.results_page_release_candidates import (
    canonical_same_origin_url,
    extract_results_page_candidates,
)
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
        self._no_release_reason: str | None = None

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

    def describe_no_release(self) -> str | None:
        """Why the last ``discover`` call selected nothing, for the audit log.

        ``no_release`` is the correct status for every outcome here - nothing was
        published that this provider is willing to ingest - but "no release
        link at all", "several releases matched" and "none carried the required
        evidence" need very different corrections. ``EventReleaseMonitor``
        records this alongside the unchanged status so an operator can tell them
        apart instead of guessing which one happened.
        """
        return self._no_release_reason

    def discover(self, event_id: str) -> ReleaseDocument | None:
        if event_id != self.source.event_id:
            raise ValueError("results page official release source event_id mismatch")
        self._no_release_reason = None
        if self.source.source_kind != "results_page":
            return None

        html_text, page_url = self._fetch_results_page_html()
        candidates = extract_results_page_candidates(self.source, html_text, page_url=page_url)
        if not candidates:
            self._no_release_reason = (
                f"results_page selection no_candidates: {page_url} offered no usable same-origin "
                "HTTPS release link"
            )
            return None

        selection = select_results_page_release_candidate(
            self.selection_context,
            candidates,
            release_period=self.release_period,
        )
        if selection.status is not ResultsPageSelectionStatus.SELECTED or selection.candidate is None:
            self._no_release_reason = self._selection_reason(
                selection.status, page_url, len(candidates)
            )
            return None
        candidate = selection.candidate

        # Defence in depth. The extractor already refuses any candidate that
        # leaves the approved origin; re-checking here means a future change to
        # candidate construction cannot quietly widen what this provider will
        # download.
        self._validate_final_url(self.source.source_url, candidate.source_url)

        data, content_type, http_charset, served_url = self._fetch_resource(candidate.source_url)
        document_url = canonical_same_origin_url(self.source.source_url, served_url)
        if document_url is None:
            raise RuntimeError(
                "results page official release document left the approved HTTPS origin"
            )
        if document_url in self._results_page_urls(page_url):
            # A candidate that redirects back to the results page is not a
            # release. Without this the listing page itself would be read as a
            # release document whenever it carries enough visible text, and
            # persisted under a URL it was never served from. Nothing is
            # interpreted or stored: the bytes are dropped here.
            self._no_release_reason = (
                f"results_page selected document redirected to the results page: "
                f"{candidate.source_url} was served from {document_url}, which is the results "
                "page itself, not a release document"
            )
            return None

        raw_text, source_type = self._interpret_document(
            document_url,
            data,
            content_type,
            http_charset,
        )

        raw_text = raw_text.strip()
        if len(raw_text) < self.MIN_DOCUMENT_CHARS:
            self._no_release_reason = (
                f"results_page selected document too short: {document_url} yielded "
                f"{len(raw_text)} characters, below the {self.MIN_DOCUMENT_CHARS} character minimum"
            )
            return None

        return ReleaseDocument(
            event_id=event_id,
            source_type=source_type,
            # The URL the document was actually served from, not the link that
            # pointed at it. Only this provider resolves a link it did not
            # choose, so only this provider needs the distinction; the
            # direct_url contract is deliberately left alone.
            source_url=document_url,
            source_title=self._document_title(candidate.source_title),
            raw_text=raw_text,
        )

    def _results_page_urls(self, page_url: str) -> frozenset[str]:
        """Both canonical spellings of the results page: approved and served."""
        canonical = (
            canonical_same_origin_url(self.source.source_url, self.source.source_url),
            canonical_same_origin_url(self.source.source_url, page_url),
        )
        return frozenset(url for url in canonical if url is not None)

    def _selection_reason(
        self,
        status: ResultsPageSelectionStatus,
        page_url: str,
        candidate_count: int,
    ) -> str:
        evidence = (
            f"scheduled_date={self.selection_context.scheduled_date.isoformat()}, "
            f"release_period={self.release_period or '<none>'}"
        )
        if status is ResultsPageSelectionStatus.AMBIGUOUS:
            return (
                f"results_page selection ambiguous: more than one of the {candidate_count} candidates on "
                f"{page_url} matched {evidence}; refusing to guess which release is the right one"
            )
        return (
            f"results_page selection no_match: none of the {candidate_count} candidates on {page_url} "
            f"carried {evidence} evidence"
        )

    def _document_title(self, candidate_title: str | None) -> str:
        """Prefer the link text the page itself gave the selected release."""
        for value in (candidate_title, self.source.source_title):
            cleaned = (value or "").strip()
            if cleaned:
                return cleaned
        return "results-page-official-release"

    def _fetch_results_page_html(self) -> tuple[str, str]:
        """Download the approved results page as HTML, decoded by its charset.

        Returns the markup together with the redirect-validated URL it was
        served from, because the page's relative links resolve against that URL
        and not against the approved URL a same-origin redirect may have moved
        away from.

        The raw markup is what the candidate extractor needs, so this
        deliberately does not run the visible-text extraction the release
        document path uses. A results page that is not markup - a PDF, a plain
        text body, anything the HTML sniffing does not recognise - fails closed
        rather than being handed to the extractor.
        """
        data, content_type, http_charset, page_url = self._fetch_resource(self.source.source_url)
        media_type = self._media_type(content_type)
        if media_type not in {"", "text/html", "application/xhtml+xml"} or not self._looks_like_html_or_text(
            content_type, data
        ):
            raise RuntimeError(
                f"results page official release returned unsupported content type: {media_type or '<missing>'}"
            )
        html_text = self._decode_text(
            data,
            http_charset,
            allow_html_meta=True,
            allow_xml_declaration=media_type == "application/xhtml+xml",
        )
        return html_text, page_url
