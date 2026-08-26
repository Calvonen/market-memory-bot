from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

from trading_system.official_release_source_repository import OfficialReleaseSource, _is_valid_host


@dataclass(frozen=True)
class ResultsPageReleaseCandidate:
    event_id: str
    source_url: str
    source_title: str | None = None


class _ResultsPageLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str | None]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = {key.lower(): value for key, value in attrs if value is not None}
        self._href = values.get("href")
        self._parts = [values.get("aria-label") or "", values.get("title") or ""]

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        title = " ".join(" ".join(self._parts).split()) or None
        self.links.append((self._href, title))
        self._href = None
        self._parts = []


def _https_origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
        host = parsed.hostname or ""
    except ValueError:
        return None

    # Preserve the approved authority spelling exactly at the validation
    # boundary. A trailing dot is a different authority spelling and must not
    # be silently collapsed into the approved origin.
    if (
        parsed.scheme.lower() != "https"
        or not host
        or host.endswith(".")
        or not _is_valid_host(host)
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None
    return "https", host.lower(), 443 if port is None else port


def _remove_last_path_segment(path: str) -> str:
    slash = path.rfind("/")
    return "" if slash < 0 else path[:slash]


def _remove_dot_segments(path: str) -> str:
    """Remove URI dot segments using the RFC 3986 section 5.2.4 algorithm."""
    input_buffer = path
    output = ""

    while input_buffer:
        if input_buffer.startswith("../"):
            input_buffer = input_buffer[3:]
        elif input_buffer.startswith("./"):
            input_buffer = input_buffer[2:]
        elif input_buffer.startswith("/./"):
            input_buffer = "/" + input_buffer[3:]
        elif input_buffer == "/.":
            input_buffer = "/"
        elif input_buffer.startswith("/../"):
            input_buffer = "/" + input_buffer[4:]
            output = _remove_last_path_segment(output)
        elif input_buffer == "/..":
            input_buffer = "/"
            output = _remove_last_path_segment(output)
        elif input_buffer in {".", ".."}:
            input_buffer = ""
        else:
            if input_buffer.startswith("/"):
                next_slash = input_buffer.find("/", 1)
            else:
                next_slash = input_buffer.find("/")
            if next_slash < 0:
                output += input_buffer
                input_buffer = ""
            else:
                output += input_buffer[:next_slash]
                input_buffer = input_buffer[next_slash:]

    return output


def _normalize_url_path(path: str) -> str:
    normalized = _remove_dot_segments(path or "/")
    return normalized or "/"


def _canonical_https_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
        host = parsed.hostname or ""
    except ValueError:
        return None

    origin = _https_origin(url)
    if origin is None:
        return None

    canonical_host = host.lower()
    try:
        is_ipv6 = isinstance(ipaddress.ip_address(canonical_host), ipaddress.IPv6Address)
    except ValueError:
        is_ipv6 = False
    rendered_host = f"[{canonical_host}]" if is_ipv6 else canonical_host
    canonical_netloc = rendered_host if port in (None, 443) else f"{rendered_host}:{port}"
    canonical_path = _normalize_url_path(parsed.path)
    return urlunparse(
        (
            "https",
            canonical_netloc,
            canonical_path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def _contains_ascii_control(value: str) -> bool:
    return any(ord(char) <= 0x1F or ord(char) == 0x7F for char in value)


def _canonical_candidate_url(base_url: str, href: str) -> str | None:
    # URL parsers and whitespace trimming can normalize raw controls away.
    # Reject the complete ASCII control range, including DEL, before either
    # operation so malformed href spellings always fail closed.
    if _contains_ascii_control(href):
        return None
    raw_href = href.strip()

    try:
        candidate = urljoin(base_url, raw_href)
    except ValueError:
        return None

    approved_origin = _https_origin(base_url)
    candidate_origin = _https_origin(candidate)
    if approved_origin is None or candidate_origin != approved_origin:
        return None
    return _canonical_https_url(candidate)


def extract_results_page_candidates(
    source: OfficialReleaseSource,
    html_text: str,
) -> tuple[ResultsPageReleaseCandidate, ...]:
    """Extract same-origin HTTPS links from an approved results page.

    This function intentionally does not decide which link is the release and
    does not mutate the approved source. It only exposes deterministic
    candidates for a later, explicit selection step.
    """
    if source.source_kind != "results_page":
        raise ValueError("results page candidate extraction requires source_kind=results_page")

    parser = _ResultsPageLinkParser()
    parser.feed(html_text)

    page_url = _canonical_candidate_url(source.source_url, source.source_url)
    seen: set[str] = set()
    candidates: list[ResultsPageReleaseCandidate] = []
    for href, title in parser.links:
        candidate_url = _canonical_candidate_url(source.source_url, href)
        if candidate_url is None or candidate_url == page_url or candidate_url in seen:
            continue
        seen.add(candidate_url)
        candidates.append(
            ResultsPageReleaseCandidate(
                event_id=source.event_id,
                source_url=candidate_url,
                source_title=title,
            )
        )
    return tuple(candidates)
