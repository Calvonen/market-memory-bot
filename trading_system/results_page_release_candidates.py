from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

from trading_system.official_release_source_repository import OfficialReleaseSource, _is_valid_host


_RAW_HREF_RE = re.compile(
    r"\bhref\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))",
    re.IGNORECASE,
)
_NUMERIC_ENTITY_RE = re.compile(r"&#(?:x([0-9a-fA-F]+)|([0-9]+));?")


@dataclass(frozen=True)
class ResultsPageReleaseCandidate:
    event_id: str
    source_url: str
    source_title: str | None = None
    evidence_fields: tuple[str, ...] = ()


def _contains_ascii_control(value: str) -> bool:
    return any(ord(char) <= 0x1F or ord(char) == 0x7F for char in value)


def _raw_href_contains_encoded_control(raw_href: str) -> bool:
    if _contains_ascii_control(raw_href):
        return True
    if "&Tab;" in raw_href or "&NewLine;" in raw_href:
        return True
    for match in _NUMERIC_ENTITY_RE.finditer(raw_href):
        raw_codepoint = match.group(1) or match.group(2)
        base = 16 if match.group(1) is not None else 10
        try:
            codepoint = int(raw_codepoint, base)
        except ValueError:
            return True
        if codepoint <= 0x1F or codepoint == 0x7F:
            return True
    return False


def _raw_href_is_safe(raw_start_tag: str, decoded_href: str | None) -> bool:
    if decoded_href is None:
        return True
    matches = list(_RAW_HREF_RE.finditer(raw_start_tag))
    if len(matches) != 1:
        return False
    match = matches[0]
    raw_href = next((value for value in match.groups() if value is not None), "")
    return not _raw_href_contains_encoded_control(raw_href)


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


class _ResultsPageLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str | None, bool, tuple[str, ...]]] = []
        self._href: str | None = None
        self._raw_href_safe = True
        self._aria_label: str | None = None
        self._title_attr: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "br" and self._href is not None:
            # A line break is an unambiguous rendered separator. Preserve it as
            # whitespace while ordinary inline wrappers keep text adjacency.
            self._text_parts.append(" ")
            return
        if normalized_tag != "a":
            return
        values = {key.lower(): value for key, value in attrs if value is not None}
        self._href = values.get("href")
        self._raw_href_safe = _raw_href_is_safe(self.get_starttag_text(), self._href)
        self._aria_label = values.get("aria-label")
        self._title_attr = values.get("title")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        # Preserve adjacency between HTML text nodes. Real whitespace and
        # explicit rendered separators remain in the chunks and are normalized
        # afterwards, but adjacent inline elements must not manufacture a token
        # boundary.
        visible_text = _normalized_text("".join(self._text_parts))
        evidence_fields = tuple(
            value
            for value in (
                _normalized_text(self._aria_label or ""),
                _normalized_text(self._title_attr or ""),
                visible_text,
            )
            if value
        )
        title = " ".join(evidence_fields) or None
        self.links.append((self._href, title, self._raw_href_safe, evidence_fields))
        self._href = None
        self._raw_href_safe = True
        self._aria_label = None
        self._title_attr = None
        self._text_parts = []


def _https_origin(url: str) -> tuple[str, str, int] | None:
    if _contains_ascii_control(url):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = parsed.hostname or ""
    except ValueError:
        return None

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


def _merge_paths(base_path: str, reference_path: str, base_has_authority: bool) -> str:
    if base_has_authority and not base_path:
        return "/" + reference_path
    slash = base_path.rfind("/")
    prefix = "" if slash < 0 else base_path[: slash + 1]
    return prefix + reference_path


def _resolve_reference(base_url: str, href: str) -> str | None:
    try:
        base = urlsplit(base_url)
        reference = urlsplit(href)
    except ValueError:
        return None

    if reference.scheme:
        target_scheme = reference.scheme
        target_netloc = reference.netloc
        target_path = _remove_dot_segments(reference.path)
        target_query = reference.query
    else:
        target_scheme = base.scheme
        if reference.netloc:
            target_netloc = reference.netloc
            target_path = _remove_dot_segments(reference.path)
            target_query = reference.query
        else:
            target_netloc = base.netloc
            if not reference.path:
                target_path = base.path
                target_query = reference.query if reference.query else base.query
            else:
                if reference.path.startswith("/"):
                    merged_path = reference.path
                else:
                    merged_path = _merge_paths(base.path, reference.path, bool(base.netloc))
                target_path = _remove_dot_segments(merged_path)
                target_query = reference.query

    return urlunsplit(
        (target_scheme, target_netloc, target_path, target_query, reference.fragment)
    )


def _canonical_https_url(url: str) -> str | None:
    if _contains_ascii_control(url):
        return None
    try:
        parsed = urlsplit(url)
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
    return urlunsplit(("https", canonical_netloc, canonical_path, parsed.query, ""))


def _canonical_candidate_url(base_url: str, href: str) -> str | None:
    if _contains_ascii_control(base_url) or _contains_ascii_control(href):
        return None
    raw_href = href.strip()

    approved_origin = _https_origin(base_url)
    if approved_origin is None:
        return None
    candidate = _resolve_reference(base_url, raw_href)
    if candidate is None:
        return None
    candidate_origin = _https_origin(candidate)
    if candidate_origin != approved_origin:
        return None
    return _canonical_https_url(candidate)


def extract_results_page_candidates(
    source: OfficialReleaseSource,
    html_text: str,
) -> tuple[ResultsPageReleaseCandidate, ...]:
    """Extract deterministic same-origin HTTPS candidates from a results page."""
    if source.source_kind != "results_page":
        raise ValueError("results page candidate extraction requires source_kind=results_page")

    page_url = _canonical_candidate_url(source.source_url, source.source_url)
    if page_url is None:
        return ()

    parser = _ResultsPageLinkParser()
    parser.feed(html_text)

    seen: set[str] = set()
    candidates: list[ResultsPageReleaseCandidate] = []
    for href, title, raw_href_safe, evidence_fields in parser.links:
        if not raw_href_safe:
            continue
        candidate_url = _canonical_candidate_url(source.source_url, href)
        if candidate_url is None or candidate_url == page_url or candidate_url in seen:
            continue
        seen.add(candidate_url)
        candidates.append(
            ResultsPageReleaseCandidate(
                event_id=source.event_id,
                source_url=candidate_url,
                source_title=title,
                evidence_fields=evidence_fields,
            )
        )
    return tuple(candidates)
