from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

import html5lib

from trading_system.official_release_source_repository import OfficialReleaseSource, _is_valid_host


_HTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_HTML_WHITESPACE = frozenset({"\t", "\n", "\f", "\r", " "})
_RAW_HREF_RE = re.compile(
    r"\bhref\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))",
    re.IGNORECASE,
)
_NUMERIC_ENTITY_RE = re.compile(r"&#(?:[xX]([0-9a-fA-F]+)|([0-9]+));?")
_RENDERED_BREAK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "center", "dd", "details", "dialog",
        "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1",
        "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "listing", "main",
        "menu", "nav", "ol", "p", "plaintext", "pre", "search", "section", "summary", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "xmp",
    }
)
_ALWAYS_NON_RENDERED_TAGS = frozenset({"script", "style", "title"})
_HTML_NON_RENDERED_TAGS = frozenset(
    {"audio", "canvas", "iframe", "noembed", "noframes", "noscript", "object", "template", "video"}
)
_SVG_NON_RENDERED_TAGS = frozenset({"desc", "metadata"})


@dataclass(frozen=True)
class ResultsPageReleaseCandidate:
    event_id: str
    source_url: str
    source_title: str | None = None
    evidence_fields: tuple[str, ...] = ()


def _contains_ascii_control(value: str) -> bool:
    return any(ord(char) <= 0x1F or ord(char) == 0x7F for char in value)


def _contains_text_control(value: str) -> bool:
    return any(
        unicodedata.category(char) == "Cc" and char not in _HTML_WHITESPACE
        for char in value
    )


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


def _safe_normalized_text(value: str) -> str | None:
    if _contains_text_control(value):
        return None
    return _normalized_text(value)


def _is_unicode_noncharacter(codepoint: int) -> bool:
    return (
        0xFDD0 <= codepoint <= 0xFDEF
        or (0 <= codepoint <= 0x10FFFF and codepoint & 0xFFFF in {0xFFFE, 0xFFFF})
    )


def _preserve_invalid_scalars_as_control(html_text: str) -> str:
    """Keep malformed/control evidence detectable instead of accepting parser remapping boundaries."""

    html_text = "".join(
        "\u000b" if char == "\x00" or _is_unicode_noncharacter(ord(char)) else char
        for char in html_text
    )

    def replace_invalid(match: re.Match[str]) -> str:
        raw_codepoint = match.group(1) or match.group(2)
        base = 16 if match.group(1) is not None else 10
        try:
            codepoint = int(raw_codepoint, base)
        except ValueError:
            return "\u000b"
        if (
            codepoint == 0
            or 0x80 <= codepoint <= 0x9F
            or 0xD800 <= codepoint <= 0xDFFF
            or codepoint > 0x10FFFF
            or _is_unicode_noncharacter(codepoint)
        ):
            return "\u000b"
        return match.group(0)

    return _NUMERIC_ENTITY_RE.sub(replace_invalid, html_text)


class _RawAnchorHrefSafetyScanner(HTMLParser):
    """Track href spellings only; html5lib owns all HTML tree construction semantics."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.unsafe_hrefs: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        hrefs = [value for key, value in attrs if key.lower() == "href" and value is not None]
        raw_start_tag = self.get_starttag_text() or ""
        if len(hrefs) != 1 or not _raw_href_is_safe(raw_start_tag, hrefs[0] if hrefs else None):
            self.unsafe_hrefs.update(hrefs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _element_name(tag: object) -> tuple[str | None, str]:
    if not isinstance(tag, str):
        return None, ""
    if tag.startswith("{") and "}" in tag:
        namespace, local = tag[1:].split("}", 1)
        return namespace, local.lower()
    return None, tag.lower()


def _element_hidden(element) -> bool:
    return any(str(key).lower().split("}")[-1] == "hidden" for key in element.attrib)


def _element_has_attribute(element, attribute: str) -> bool:
    attribute = attribute.lower()
    return any(str(key).lower().split("}")[-1] == attribute for key in element.attrib)


def _element_is_non_rendered(element) -> bool:
    namespace, local = _element_name(element.tag)
    if namespace == _HTML_NAMESPACE and local == "dialog":
        return not _element_has_attribute(element, "open")
    if local in _ALWAYS_NON_RENDERED_TAGS:
        return True
    if namespace == _SVG_NAMESPACE and local in _SVG_NON_RENDERED_TAGS:
        return True
    return namespace == _HTML_NAMESPACE and local in _HTML_NON_RENDERED_TAGS


def _closed_details_summary(element):
    namespace, local = _element_name(element.tag)
    if (
        namespace != _HTML_NAMESPACE
        or local != "details"
        or _element_has_attribute(element, "open")
    ):
        return None
    for child in list(element):
        child_namespace, child_local = _element_name(child.tag)
        if child_namespace == _HTML_NAMESPACE and child_local == "summary":
            return child
    return False


def _visible_anchor_text(anchor) -> str | None:
    parts: list[str] = []

    def append_break() -> None:
        if parts and parts[-1] != " ":
            parts.append(" ")

    def visit(element) -> None:
        if not isinstance(element.tag, str):
            return
        if _element_hidden(element) or _element_is_non_rendered(element):
            return

        details_summary = _closed_details_summary(element)
        if details_summary is not None:
            if details_summary is not False:
                append_break()
                visit(details_summary)
                append_break()
            return

        if element.text:
            parts.append(element.text)
        for child in list(element):
            child_namespace, child_local = _element_name(child.tag)
            child_is_element = isinstance(child.tag, str)
            child_visible = (
                child_is_element
                and not _element_hidden(child)
                and not _element_is_non_rendered(child)
            )
            rendered_break = (
                child_visible
                and child_namespace == _HTML_NAMESPACE
                and child_local in _RENDERED_BREAK_TAGS
            )
            if rendered_break:
                append_break()
            visit(child)
            if rendered_break:
                append_break()
            if child.tail:
                parts.append(child.tail)

    visit(anchor)
    return _safe_normalized_text("".join(parts))


def _iter_visible_html_anchors(root):
    def walk(element, hidden_ancestor: bool):
        if not isinstance(element.tag, str):
            return
        hidden_here = hidden_ancestor or _element_hidden(element)
        namespace, local = _element_name(element.tag)
        non_rendered = _element_is_non_rendered(element)
        if hidden_here or non_rendered:
            return
        if namespace == _HTML_NAMESPACE and local == "a":
            yield element

        details_summary = _closed_details_summary(element)
        if details_summary is not None:
            if details_summary is not False:
                yield from walk(details_summary, hidden_here)
            return

        for child in list(element):
            yield from walk(child, hidden_here)

    yield from walk(root, False)


def _parse_html5_links(html_text: str) -> list[tuple[str, str | None, tuple[str, ...]]]:
    safety_scanner = _RawAnchorHrefSafetyScanner()
    safety_scanner.feed(html_text)
    safety_scanner.close()

    fragment = html5lib.parseFragment(
        _preserve_invalid_scalars_as_control(html_text),
        treebuilder="etree",
        namespaceHTMLElements=True,
    )

    links: list[tuple[str, str | None, tuple[str, ...]]] = []
    for anchor in _iter_visible_html_anchors(fragment):
        href = anchor.attrib.get("href")
        if href is None or href in safety_scanner.unsafe_hrefs or _contains_ascii_control(href):
            continue
        aria_label = _safe_normalized_text(anchor.attrib.get("aria-label", ""))
        title_attr = _safe_normalized_text(anchor.attrib.get("title", ""))
        visible_text = _visible_anchor_text(anchor)
        evidence_fields = tuple(
            value for value in (aria_label, title_attr, visible_text) if value
        )
        title = " ".join(evidence_fields) or None
        links.append((href, title, evidence_fields))
    return links


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
            next_slash = input_buffer.find("/", 1) if input_buffer.startswith("/") else input_buffer.find("/")
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
                merged_path = reference.path if reference.path.startswith("/") else _merge_paths(base.path, reference.path, bool(base.netloc))
                target_path = _remove_dot_segments(merged_path)
                target_query = reference.query

    return urlunsplit((target_scheme, target_netloc, target_path, target_query, reference.fragment))


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
    """Extract deterministic same-origin HTTPS candidates from an HTML5-repaired results page."""
    if source.source_kind != "results_page":
        raise ValueError("results page candidate extraction requires source_kind=results_page")

    page_url = _canonical_candidate_url(source.source_url, source.source_url)
    if page_url is None:
        return ()

    aggregated: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for href, title, evidence_fields in _parse_html5_links(html_text):
        candidate_url = _canonical_candidate_url(source.source_url, href)
        if candidate_url is None or candidate_url == page_url:
            continue
        if candidate_url not in aggregated:
            aggregated[candidate_url] = {"titles": [], "fields": []}
            order.append(candidate_url)
        record = aggregated[candidate_url]
        if title:
            record["titles"].append(title)
        for field in evidence_fields:
            if field not in record["fields"]:
                record["fields"].append(field)

    candidates: list[ResultsPageReleaseCandidate] = []
    for candidate_url in order:
        record = aggregated[candidate_url]
        fields = tuple(record["fields"])
        titles = record["titles"]
        candidates.append(
            ResultsPageReleaseCandidate(
                event_id=source.event_id,
                source_url=candidate_url,
                source_title=" ".join(titles) or None,
                evidence_fields=fields,
            )
        )
    return tuple(candidates)
