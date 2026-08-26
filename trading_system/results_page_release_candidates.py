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
_HTML_WHITESPACE = frozenset({"\t", "\n", "\f", "\r", " "})
_RAW_HREF_RE = re.compile(
    r"\bhref\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))",
    re.IGNORECASE,
)
_NUMERIC_ENTITY_RE = re.compile(r"&#(?:[xX]([0-9a-fA-F]+)|([0-9]+));?")

_SIMPLE_HTML_TEXT_TAGS = frozenset(
    {
        "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo", "blockquote", "br",
        "button", "caption", "center", "cite", "code", "data", "dd", "del", "details", "dfn",
        "dialog", "div", "dl", "dt", "em", "fieldset", "figcaption", "figure", "footer", "form",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "i", "ins", "kbd",
        "label", "legend", "li", "listing", "main", "mark", "menu", "nav", "ol", "p", "plaintext",
        "pre", "q", "s", "samp", "search", "section", "small", "span", "strong", "sub", "summary",
        "sup", "table", "tbody", "td", "tfoot", "th", "thead", "time", "tr", "u", "ul", "var",
        "wbr", "xmp",
    }
)
_FOREIGN_ROOT_TAGS = frozenset({"svg", "math"})
_SVG_HTML_INTEGRATION_POINT_TAGS = frozenset({"desc", "foreignobject", "title"})
_MATHML_TEXT_INTEGRATION_POINT_TAGS = frozenset({"mi", "mn", "mo", "ms", "mtext"})
_INTEGRATION_NAME_COLLISIONS = frozenset(
    {*_SVG_HTML_INTEGRATION_POINT_TAGS, *_MATHML_TEXT_INTEGRATION_POINT_TAGS, "annotation-xml"}
)
_FOREIGN_BREAKOUT_TAGS = frozenset(
    {
        "b", "big", "blockquote", "body", "br", "center", "code", "dd", "div", "dl", "dt",
        "em", "embed", "font", "h1", "h2", "h3", "h4", "h5", "h6", "head", "hr", "i", "img",
        "li", "listing", "menu", "meta", "nobr", "ol", "p", "pre", "ruby", "s", "small", "span",
        "strong", "strike", "sub", "sup", "table", "tt", "u", "ul", "var",
    }
)
_HTML_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
# A special HTML element above an integration point prevents an unrelated end tag
# from reaching that integration point under the HTML "any other end tag" rules.
# We only need these scope barriers while an integration point is active; ordinary
# phrasing elements are intentionally not tracked so this scanner does not become
# a second tree builder.
_HTML_SCOPE_BARRIER_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "button", "caption", "center", "dd", "details",
        "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hgroup", "li", "listing", "main", "marquee", "menu", "nav",
        "noembed", "noframes", "noscript", "object", "ol", "p", "plaintext", "pre", "script", "search",
        "section", "select", "style", "summary", "table", "tbody", "td", "textarea", "tfoot", "th", "thead",
        "title", "tr", "ul", "xmp",
    }
)
_RENDERED_BREAK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "caption", "center", "dd", "details", "dialog",
        "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "hgroup", "hr", "legend", "li", "listing", "main", "menu", "nav",
        "ol", "p", "plaintext", "pre", "search", "section", "summary", "table", "tbody", "td", "tfoot",
        "th", "thead", "tr", "ul", "xmp",
    }
)


@dataclass(frozen=True)
class ResultsPageReleaseCandidate:
    event_id: str
    source_url: str
    source_title: str | None = None
    evidence_fields: tuple[str, ...] = ()


def _contains_ascii_control(value: str) -> bool:
    return any(ord(char) <= 0x1F or ord(char) == 0x7F for char in value)


def _contains_text_control(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" and char not in _HTML_WHITESPACE for char in value)


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
    raw_href = next((value for value in matches[0].groups() if value is not None), "")
    return not _raw_href_contains_encoded_control(raw_href)


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _safe_normalized_text(value: str) -> str | None:
    if _contains_text_control(value):
        return None
    return _normalized_text(value)


def _is_unicode_noncharacter(codepoint: int) -> bool:
    return 0xFDD0 <= codepoint <= 0xFDEF or (
        0 <= codepoint <= 0x10FFFF and codepoint & 0xFFFF in {0xFFFE, 0xFFFF}
    )


def _literal_requires_rejection_sentinel(char: str) -> bool:
    codepoint = ord(char)
    return (
        char == "\x00"
        or 0xD800 <= codepoint <= 0xDFFF
        or _is_unicode_noncharacter(codepoint)
        or (unicodedata.category(char) == "Cc" and char not in _HTML_WHITESPACE)
    )


def _preserve_invalid_scalars_as_control(html_text: str) -> str:
    html_text = "".join("\u000b" if _literal_requires_rejection_sentinel(char) else char for char in html_text)

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
    """Track raw anchor occurrences while html5lib remains tree-construction authority."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.unsafe_hrefs: set[str] = set()
        self.anchor_occurrences: dict[str, list[bool]] = {}
        # (kind, local-name), newest last. This is deliberately scope bookkeeping,
        # not a general DOM stack.
        self._scope_stack: list[tuple[str, str]] = []
        self._last_start_was_foreign = False

    _FOREIGN_KINDS = frozenset({"foreign_root", "integration", "template_foreign"})
    _TEMPLATE_KINDS = frozenset({"template_html", "template_foreign"})

    @property
    def template_hrefs(self) -> set[str]:
        return {href for href, flags in self.anchor_occurrences.items() if any(flags)}

    def _record_anchor(self, attrs: list[tuple[str, str | None]]) -> None:
        hrefs = [value for key, value in attrs if key.lower() == "href" and value is not None]
        raw_start_tag = self.get_starttag_text() or ""
        if len(hrefs) != 1 or not _raw_href_is_safe(raw_start_tag, hrefs[0] if hrefs else None):
            self.unsafe_hrefs.update(hrefs)
        for href in hrefs:
            self.anchor_occurrences.setdefault(href, []).append(self._inside_html_template())

    def _inside_html_template(self) -> bool:
        return any(kind == "template_html" for kind, _ in self._scope_stack)

    def _start_tag_mode(self) -> str:
        if not self._scope_stack:
            return "html"
        return "foreign" if self._scope_stack[-1][0] in ("foreign_root", "template_foreign") else "html"

    def _end_tag_mode(self) -> str:
        if not self._scope_stack:
            return "html"
        return "foreign" if self._scope_stack[-1][0] in self._FOREIGN_KINDS else "html"

    def _current_foreign_root(self) -> str | None:
        for kind, name in reversed(self._scope_stack):
            if kind == "foreign_root":
                return name
        return None

    @staticmethod
    def _attribute_value(attrs: list[tuple[str, str | None]], wanted: str) -> str | None:
        wanted = wanted.lower()
        for key, value in attrs:
            if key.lower() == wanted:
                return value
        return None

    def _is_html_integration_point(self, lowered: str, attrs: list[tuple[str, str | None]]) -> bool:
        root = self._current_foreign_root()
        if root == "svg":
            return lowered in _SVG_HTML_INTEGRATION_POINT_TAGS
        if root != "math":
            return False
        if lowered in _MATHML_TEXT_INTEGRATION_POINT_TAGS:
            return True
        if lowered != "annotation-xml":
            return False
        encoding = self._attribute_value(attrs, "encoding")
        if encoding is None:
            return False
        return encoding.strip().lower() in {"text/html", "application/xhtml+xml"}

    def _integration_active(self) -> bool:
        return any(kind == "integration" for kind, _ in self._scope_stack)

    def _open_element(
        self,
        lowered: str,
        attrs: list[tuple[str, str | None]],
        self_closing: bool,
    ) -> None:
        mode = self._start_tag_mode()

        if lowered in _FOREIGN_ROOT_TAGS:
            self._last_start_was_foreign = True
            if not self_closing:
                self._scope_stack.append(("foreign_root", lowered))
            return

        if mode == "foreign":
            if self._is_html_integration_point(lowered, attrs):
                self._last_start_was_foreign = True
                if not self_closing:
                    self._scope_stack.append(("integration", lowered))
                return
            if lowered in _FOREIGN_BREAKOUT_TAGS:
                while self._scope_stack and self._scope_stack[-1][0] in ("foreign_root", "template_foreign"):
                    self._scope_stack.pop()
                self._last_start_was_foreign = False
                mode = "html"
            else:
                self._last_start_was_foreign = True
                if lowered == "template" and not self_closing:
                    self._scope_stack.append(("template_foreign", lowered))
                return

        self._last_start_was_foreign = False
        if lowered == "template":
            # HTML ignores the self-closing flag on non-void template.
            self._scope_stack.append(("template_html", lowered))
        elif lowered in _INTEGRATION_NAME_COLLISIONS:
            # In HTML mode this is an ordinary HTML element sharing a name with a
            # foreign integration point. Its end tag must close this occurrence,
            # not the actual integration point underneath it.
            self._scope_stack.append(("html_shadow", lowered))
        elif (
            self._integration_active()
            and lowered in _HTML_SCOPE_BARRIER_TAGS
            and lowered not in _HTML_VOID_TAGS
        ):
            # A special HTML child above an integration point blocks an unrelated
            # integration-point end tag from reaching the foreign node. Tracking
            # only these barriers keeps malformed input fail-closed without
            # reproducing the whole HTML open-elements stack.
            self._scope_stack.append(("html_barrier", lowered))

    def _close_foreign(self, lowered: str) -> bool:
        for index in range(len(self._scope_stack) - 1, -1, -1):
            kind, name = self._scope_stack[index]
            if kind in self._FOREIGN_KINDS:
                if name == lowered:
                    del self._scope_stack[index:]
                    return True
                continue
            return False
        return True

    def _close_html(self, lowered: str) -> None:
        if lowered == "template":
            for index in range(len(self._scope_stack) - 1, -1, -1):
                if self._scope_stack[index][0] in self._TEMPLATE_KINDS:
                    del self._scope_stack[index:]
                    return
            return

        for index in range(len(self._scope_stack) - 1, -1, -1):
            kind, name = self._scope_stack[index]
            if kind in {"html_shadow", "html_barrier"}:
                if name == lowered:
                    del self._scope_stack[index:]
                    return
                if kind == "html_barrier":
                    # A special HTML node encountered before the requested element
                    # makes the end tag a parse error; do not release underlying
                    # foreign/integration/template scope.
                    return
                continue
            return

    def set_cdata_mode(self, elem: str, **kwargs) -> None:
        if self._last_start_was_foreign:
            return
        super().set_cdata_mode(elem, **kwargs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        self._open_element(lowered, attrs, self_closing=False)
        if lowered == "a":
            self._record_anchor(attrs)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        self._last_start_was_foreign = False
        if self._end_tag_mode() == "foreign" and self._close_foreign(lowered):
            return
        self._close_html(lowered)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        self._open_element(lowered, attrs, self_closing=True)
        if lowered == "a":
            self._record_anchor(attrs)


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


def _element_allows_simple_text(element) -> bool:
    namespace, local = _element_name(element.tag)
    if namespace != _HTML_NAMESPACE or local not in _SIMPLE_HTML_TEXT_TAGS:
        return False
    if local == "dialog" and not _element_has_attribute(element, "open"):
        return False
    return True


def _closed_details_summary(element):
    namespace, local = _element_name(element.tag)
    if namespace != _HTML_NAMESPACE or local != "details" or _element_has_attribute(element, "open"):
        return None
    for child in list(element):
        child_namespace, child_local = _element_name(child.tag)
        if child_namespace == _HTML_NAMESPACE and child_local == "summary":
            return child
    return False


def _visible_anchor_text_fields(anchor) -> tuple[str, ...]:
    fragments: list[list[str]] = [[]]

    def append_text(value: str) -> None:
        fragments[-1].append(value)

    def append_break() -> None:
        current = fragments[-1]
        if current and current[-1] != " ":
            current.append(" ")

    def split_ambiguous_boundary() -> None:
        if fragments[-1]:
            fragments.append([])

    def visit(element) -> None:
        if not isinstance(element.tag, str):
            return
        if _element_hidden(element) or not _element_allows_simple_text(element):
            return

        details_summary = _closed_details_summary(element)
        if details_summary is not None:
            if details_summary is not False:
                append_break()
                visit(details_summary)
                append_break()
            return

        if element.text:
            append_text(element.text)
        for child in list(element):
            child_namespace, child_local = _element_name(child.tag)
            child_is_element = isinstance(child.tag, str)
            child_hidden = child_is_element and _element_hidden(child)
            child_visible = child_is_element and not child_hidden and _element_allows_simple_text(child)
            rendered_break = (
                child_visible and child_namespace == _HTML_NAMESPACE and child_local in _RENDERED_BREAK_TAGS
            )
            if rendered_break:
                append_break()
            if child_visible:
                visit(child)
            elif child_is_element and not child_hidden:
                split_ambiguous_boundary()
            if rendered_break:
                append_break()
            if child.tail:
                append_text(child.tail)

    visit(anchor)
    fields: list[str] = []
    for fragment in fragments:
        normalized = _safe_normalized_text("".join(fragment))
        if normalized:
            fields.append(normalized)
    return tuple(fields)


def _element_is_foreign(element) -> bool:
    namespace, _ = _element_name(element.tag)
    return namespace is not None and namespace != _HTML_NAMESPACE


def _iter_all_tree_anchors(root):
    for element in root.iter():
        _, local = _element_name(element.tag)
        if local == "a":
            yield element


def _template_local_tree_anchors(root, safety_scanner) -> set:
    template_hrefs = safety_scanner.template_hrefs
    if not template_hrefs:
        return set()

    grouped: dict[str, list] = {}
    for anchor in _iter_all_tree_anchors(root):
        href = anchor.attrib.get("href")
        if href is None or href not in template_hrefs:
            continue
        grouped.setdefault(href, []).append(anchor)

    suppressed = set()
    for href, anchors in grouped.items():
        occurrences = safety_scanner.anchor_occurrences[href]
        if len(anchors) != len(occurrences):
            suppressed.update(anchors)
            continue
        suppressed.update(
            anchor for anchor, is_template_local in zip(anchors, occurrences) if is_template_local
        )
    return suppressed


def _iter_visible_html_anchors(root):
    def walk(element, is_fragment_root: bool = False):
        if not isinstance(element.tag, str):
            return
        if not is_fragment_root:
            if _element_hidden(element):
                return
            if not _element_allows_simple_text(element):
                if not _element_is_foreign(element):
                    return
            else:
                namespace, local = _element_name(element.tag)
                if namespace == _HTML_NAMESPACE and local == "a":
                    yield element

                details_summary = _closed_details_summary(element)
                if details_summary is not None:
                    if details_summary is not False:
                        yield from walk(details_summary)
                    return

        for child in list(element):
            yield from walk(child)

    yield from walk(root, True)


def _parse_html5_links(html_text: str) -> list[tuple[str, str | None, tuple[str, ...]]]:
    safety_scanner = _RawAnchorHrefSafetyScanner()
    safety_scanner.feed(html_text)
    safety_scanner.close()

    fragment = html5lib.parseFragment(
        _preserve_invalid_scalars_as_control(html_text),
        treebuilder="etree",
        namespaceHTMLElements=True,
    )
    template_local_anchors = _template_local_tree_anchors(fragment, safety_scanner)

    links: list[tuple[str, str | None, tuple[str, ...]]] = []
    for anchor in _iter_visible_html_anchors(fragment):
        href = anchor.attrib.get("href")
        if href is None or href in safety_scanner.unsafe_hrefs or _contains_ascii_control(href):
            continue
        if anchor in template_local_anchors:
            continue
        aria_label = _safe_normalized_text(anchor.attrib.get("aria-label", ""))
        title_attr = _safe_normalized_text(anchor.attrib.get("title", ""))
        visible_text_fields = _visible_anchor_text_fields(anchor)
        evidence_fields = tuple(value for value in (aria_label, title_attr, *visible_text_fields) if value)
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
                merged_path = (
                    reference.path
                    if reference.path.startswith("/")
                    else _merge_paths(base.path, reference.path, bool(base.netloc))
                )
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
            aggregated[candidate_url] = {"title": None, "fields": []}
            order.append(candidate_url)
        record = aggregated[candidate_url]
        if title and record["title"] is None:
            record["title"] = title
        for field in evidence_fields:
            if field not in record["fields"]:
                record["fields"].append(field)

    candidates: list[ResultsPageReleaseCandidate] = []
    for candidate_url in order:
        record = aggregated[candidate_url]
        candidates.append(
            ResultsPageReleaseCandidate(
                event_id=source.event_id,
                source_url=candidate_url,
                source_title=record["title"],
                evidence_fields=tuple(record["fields"]),
            )
        )
    return tuple(candidates)
