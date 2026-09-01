from __future__ import annotations

import ipaddress
import os
import socket
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
_SEARCH_URL = "https://finnhub.io/api/v1/search"

_MARKET_SUFFIXES: dict[str, frozenset[str]] = {
    "SWITZERLAND": frozenset({".SW"}),
    "SIX": frozenset({".SW"}),
    "ZURICH": frozenset({".SW"}),
    "UNITED KINGDOM": frozenset({".L"}),
    "UK": frozenset({".L"}),
    "LONDON": frozenset({".L"}),
    "LSE": frozenset({".L"}),
    "FINLAND": frozenset({".HE"}),
    "HELSINKI": frozenset({".HE"}),
    "SWEDEN": frozenset({".ST"}),
    "STOCKHOLM": frozenset({".ST"}),
    "NORWAY": frozenset({".OL"}),
    "OSLO": frozenset({".OL"}),
    "DENMARK": frozenset({".CO"}),
    "COPENHAGEN": frozenset({".CO"}),
    "GERMANY": frozenset({".DE"}),
    "XETRA": frozenset({".DE"}),
    "FRANCE": frozenset({".PA"}),
    "PARIS": frozenset({".PA"}),
    "NETHERLANDS": frozenset({".AS"}),
    "AMSTERDAM": frozenset({".AS"}),
    "ITALY": frozenset({".MI"}),
    "MILAN": frozenset({".MI"}),
    "SPAIN": frozenset({".MC"}),
    "MADRID": frozenset({".MC"}),
    "AUSTRALIA": frozenset({".AX"}),
    "ASX": frozenset({".AX"}),
    "SYDNEY": frozenset({".AX"}),
    "JAPAN": frozenset({".T"}),
    "TOKYO": frozenset({".T"}),
    "HONG KONG": frozenset({".HK"}),
    "HKEX": frozenset({".HK"}),
}

_LEGAL_NAME_WORDS = frozenset(
    {
        "ag",
        "asa",
        "as",
        "ab",
        "oyj",
        "oy",
        "plc",
        "ltd",
        "limited",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "sa",
        "nv",
        "se",
        "spa",
        "group",
        "holding",
        "holdings",
    }
)


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


def _default_host_resolver(hostname: str) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            rows = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return ()
        return tuple(sorted({str(row[4][0]) for row in rows if row and row[4]}))
    return (str(literal),)


def _host_resolves_publicly(hostname: str, resolver: Callable[[str], tuple[str, ...]]) -> bool:
    normalized = hostname.strip().rstrip(".").lower()
    if not normalized or normalized == "localhost" or normalized.endswith(".local"):
        return False
    addresses = resolver(normalized)
    if not addresses:
        return False
    try:
        parsed = tuple(ipaddress.ip_address(value) for value in addresses)
    except ValueError:
        return False
    return all(address.is_global for address in parsed)


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


def _ticker_root(symbol: str) -> str:
    return symbol.strip().upper().split(".", 1)[0]


def _issuer_tokens(value: str) -> tuple[str, ...]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
    return tuple(
        token
        for token in cleaned.split()
        if token and token not in _LEGAL_NAME_WORDS
    )


def _issuer_matches(canonical_name: str, candidate_name: str) -> bool:
    expected = _issuer_tokens(canonical_name)
    candidate = _issuer_tokens(candidate_name)
    if not expected or not candidate:
        return False
    expected_set = set(expected)
    candidate_set = set(candidate)
    common = expected_set & candidate_set
    required = min(2, len(expected_set))
    return len(common) >= required and (
        expected_set <= candidate_set or candidate_set <= expected_set or len(common) >= 2
    )


def _symbol_matches_market(symbol: str, market: str) -> bool:
    suffixes = _MARKET_SUFFIXES.get(market.strip().upper())
    if not suffixes:
        return False
    normalized = symbol.strip().upper()
    return any(normalized.endswith(suffix) for suffix in suffixes)


class FinnhubOfficialResultsProvider:
    """Discover an official results page without guessing issuer or network origin.

    Finnhub is used only to resolve the issuer's own public HTTPS website. A
    broker ticker may be translated to a Finnhub exchange suffix only when the
    canonical market has a known suffix mapping and both Finnhub search and
    profile issuer names match the canonical company name. Every website fetch
    is preceded by a public-IP check and subsequent navigation stays same-origin.
    """

    name = "finnhub_official_results_discovery"

    def __init__(
        self,
        *,
        event_id: str,
        ticker: str,
        scheduled_date: date,
        market: str,
        company_name: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 15.0,
        http_get: Callable[..., Any] | None = None,
        host_resolver: Callable[[str], tuple[str, ...]] | None = None,
    ) -> None:
        self.event_id = event_id.strip()
        self.ticker = ticker.strip().upper()
        self.market = market.strip().upper()
        self.company_name = (company_name or "").strip()
        self.scheduled_date = scheduled_date
        self.api_key = (api_key or os.environ.get("FINNHUB_API_KEY") or "").strip()
        self.timeout_seconds = timeout_seconds
        self._http_get = http_get or requests.get
        self._host_resolver = host_resolver or _default_host_resolver
        self._no_release_reason: str | None = None
        if not self.event_id or not self.ticker or not self.market:
            raise ValueError("global release discovery requires event_id, ticker and market")
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
            self._no_release_reason = "global discovery could not resolve a verified public HTTPS company website"
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
        direct = self._profile_homepage(self.ticker, require_issuer_match=bool(self.company_name))
        if direct is not None:
            return direct
        alternate = self._unique_verified_finnhub_symbol()
        if alternate is None or alternate == self.ticker:
            return None
        return self._profile_homepage(alternate, require_issuer_match=True)

    def _profile_homepage(self, symbol: str, *, require_issuer_match: bool) -> str | None:
        payload = self._get_json(
            _PROFILE_URL,
            params={"symbol": symbol, "token": self.api_key},
            operation="company profile",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Finnhub company profile returned unexpected shape")
        if require_issuer_match and not _issuer_matches(
            self.company_name,
            str(payload.get("name") or ""),
        ):
            return None
        homepage = _canonical_https_homepage(payload.get("weburl"))
        if homepage is None:
            return None
        hostname = urlparse(homepage).hostname or ""
        return homepage if _host_resolves_publicly(hostname, self._host_resolver) else None

    def _unique_verified_finnhub_symbol(self) -> str | None:
        if not self.company_name or self.market not in _MARKET_SUFFIXES:
            return None
        root = _ticker_root(self.ticker)
        payload = self._get_json(
            _SEARCH_URL,
            params={"q": root, "token": self.api_key},
            operation="symbol search",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Finnhub symbol search returned unexpected shape")
        rows = payload.get("result") or []
        if not isinstance(rows, list):
            raise RuntimeError("Finnhub symbol search returned unexpected result shape")
        matches = {
            str(row.get("symbol") or "").strip().upper()
            for row in rows
            if isinstance(row, dict)
            and str(row.get("symbol") or "").strip()
            and _ticker_root(str(row.get("symbol") or "")) == root
            and _symbol_matches_market(str(row.get("symbol") or ""), self.market)
            and _issuer_matches(self.company_name, str(row.get("description") or ""))
            and str(row.get("type") or "").strip().lower()
            in {"common stock", "equity", "stock"}
        }
        return next(iter(matches)) if len(matches) == 1 else None

    def _get_json(self, url: str, *, params: dict[str, str], operation: str) -> Any:
        try:
            response = self._http_get(
                url,
                params=params,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Finnhub {operation} request failed: {exc}") from exc
        if not response.ok:
            raise RuntimeError(f"Finnhub {operation} HTTP {response.status_code}: {response.text[:500]}")
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"Finnhub {operation} returned invalid JSON") from exc

    def _fetch_html(self, homepage: str, url: str) -> tuple[str, str]:
        hostname = urlparse(url).hostname or ""
        if not _host_resolves_publicly(hostname, self._host_resolver):
            raise RuntimeError("global release discovery refused a non-public HTTPS origin")
        source = OfficialReleaseSource(
            event_id=self.event_id,
            source_kind="results_page",
            source_url=homepage,
            source_title="automatically discovered official company website",
        )
        fetcher = ApprovedOriginDocumentFetcher(source, timeout_seconds=self.timeout_seconds)
        data, content_type, http_charset, served_url = fetcher._fetch_resource(url)
        served_hostname = urlparse(served_url).hostname or ""
        if not _host_resolves_publicly(served_hostname, self._host_resolver):
            raise RuntimeError("global release discovery redirect resolved to a non-public origin")
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
