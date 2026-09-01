from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests

from trading_system.manual_release_ingestion import ApprovedOriginDocumentFetcher
from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.release_ingestion import ReleaseDocument
from trading_system.results_page_release_candidates import canonical_same_origin_url
from trading_system.results_page_release_ingestion import ResultsPageOfficialReleaseProvider


_PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2"


@dataclass(frozen=True)
class _Link:
    url: str
    label: str


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        self._href = href.strip() if isinstance(href, str) else None
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        label = " ".join(" ".join(self._parts).split())
        self.links.append((self._href, label))
        self._href = None
        self._parts = []


def _canonical_https_homepage(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return parsed._replace(fragment="").geturl()


def _same_origin_links(homepage: str, page_url: str, html_text: str) -> tuple[_Link, ...]:
    parser = _AnchorParser()
    parser.feed(html_text)
    by_url: dict[str, list[str]] = {}
    for href, label in parser.links:
        absolute = urljoin(page_url, href)
        canonical = canonical_same_origin_url(homepage, absolute)
        if canonical is None:
            continue
        by_url.setdefault(canonical, []).append(label)
    return tuple(
        _Link(url=url, label=" ".join(label for label in labels if label).strip())
        for url, labels in by_url.items()
    )


def _score_link(link: _Link, *, stage: str) -> int:
    text = f"{link.label} {urlparse(link.url).path}".lower().replace("_", " ").replace("-", " ")
    if stage == "results":
        weighted = (
            ("results and reports", 12),
            ("financial results", 11),
            ("results reports", 10),
            ("results", 7),
            ("financial reports", 7),
            ("reports", 5),
            ("reporting", 4),
        )
    else:
        weighted = (
            ("investor relations", 12),
            ("investors", 9),
            ("investor", 7),
            ("shareholders", 5),
        )
    return max((score for phrase, score in weighted if phrase in text), default=0)


def _select_unique_best(links: tuple[_Link, ...], *, stage: str) -> str | None:
    scored = [(link, _score_link(link, stage=stage)) for link in links]
    best_score = max((score for _link, score in scored), default=0)
    if best_score <= 0:
        return None
    winners = [link.url for link, score in scored if score == best_score]
    return winners[0] if len(winners) == 1 else None


class FinnhubOfficialResultsProvider:
    """Discover an official results page globally from canonical instrument identity.

    Finnhub is used only to resolve the company's own HTTPS website. From that
    origin the provider performs a bounded, same-origin navigation: it first
    looks for a unique results/reports link, otherwise a unique investor link
    and then a unique results/reports link. Ambiguity fails closed. Once a
    results page is found, the existing reviewed ResultsPageOfficialReleaseProvider
    owns release-link selection and document ingestion.
    """

    name = "finnhub_official_results_discovery"

    def __init__(
        self,
        *,
        event_id: str,
        ticker: str,
        scheduled_date: date,
        api_key: str | None = None,
        timeout_seconds: float = 15.0,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        self.event_id = event_id.strip()
        self.ticker = ticker.strip().upper()
        self.scheduled_date = scheduled_date
        self.api_key = (api_key or os.environ.get("FINNHUB_API_KEY") or "").strip()
        self.timeout_seconds = timeout_seconds
        self._http_get = http_get or requests.get
        self._no_release_reason: str | None = None
        if not self.event_id or not self.ticker:
            raise ValueError("global release discovery requires event_id and ticker")
        if not self.api_key:
            raise ValueError("FINNHUB_API_KEY is required for global release discovery")

    def describe_no_release(self) -> str | None:
        return self._no_release_reason

    def discover(self, event_id: str) -> ReleaseDocument | None:
        if event_id != self.event_id:
            raise ValueError("global release discovery event_id mismatch")
        self._no_release_reason = None

        homepage = self._company_homepage()
        if homepage is None:
            self._no_release_reason = "global discovery could not resolve a unique official HTTPS company website"
            return None

        homepage_html, served_homepage = self._fetch_html(homepage, homepage)
        homepage_links = _same_origin_links(homepage, served_homepage, homepage_html)
        results_page = _select_unique_best(homepage_links, stage="results")

        if results_page is None:
            investor_page = _select_unique_best(homepage_links, stage="investor")
            if investor_page is None:
                self._no_release_reason = (
                    f"global discovery found no unique investor/results navigation on {served_homepage}"
                )
                return None
            investor_html, served_investor = self._fetch_html(homepage, investor_page)
            results_page = _select_unique_best(
                _same_origin_links(homepage, served_investor, investor_html),
                stage="results",
            )
            if results_page is None:
                self._no_release_reason = (
                    f"global discovery found no unique results/reports page on {served_investor}"
                )
                return None

        source = OfficialReleaseSource(
            event_id=self.event_id,
            source_kind="results_page",
            source_url=results_page,
            source_title="automatically discovered official results page",
        )
        provider = ResultsPageOfficialReleaseProvider.for_event(
            source,
            scheduled_date=self.scheduled_date,
            timeout_seconds=self.timeout_seconds,
        )
        document = provider.discover(event_id)
        if document is None:
            self._no_release_reason = provider.describe_no_release()
        return document

    def _company_homepage(self) -> str | None:
        try:
            response = self._http_get(
                _PROFILE_URL,
                params={"symbol": self.ticker, "token": self.api_key},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Finnhub company profile request failed: {exc}") from exc
        if not response.ok:
            raise RuntimeError(f"Finnhub company profile HTTP {response.status_code}: {response.text[:500]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Finnhub company profile returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Finnhub company profile returned unexpected shape")
        return _canonical_https_homepage(payload.get("weburl"))

    def _fetch_html(self, homepage: str, url: str) -> tuple[str, str]:
        source = OfficialReleaseSource(
            event_id=self.event_id,
            source_kind="results_page",
            source_url=homepage,
            source_title="automatically discovered official company website",
        )
        fetcher = ApprovedOriginDocumentFetcher(source, timeout_seconds=self.timeout_seconds)
        data, content_type, http_charset, served_url = fetcher._fetch_resource(url)
        media_type = fetcher._media_type(content_type)
        if media_type not in {"", "text/html", "application/xhtml+xml"} or not fetcher._looks_like_html_or_text(
            content_type, data
        ):
            raise RuntimeError(
                f"global release discovery returned unsupported page content type: {media_type or '<missing>'}"
            )
        html_text = fetcher._decode_text(
            data,
            http_charset,
            allow_html_meta=True,
            allow_xml_declaration=media_type == "application/xhtml+xml",
        )
        return html_text, served_url
