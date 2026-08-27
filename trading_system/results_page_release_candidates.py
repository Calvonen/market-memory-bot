from __future__ import annotations

import ipaddress
import secrets
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
# Reserved attribute prefix used to give each raw <template> start tag an identity
# html5lib carries through reparenting. Never read from user input: a page that
# spells the prefix itself refuses instrumentation and falls back to fail-closed.
_TEMPLATE_MARKER_PREFIX = "data-mmb-template-token-"
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


class _RawTemplateAndHrefScanner(HTMLParser):
    """Raw href safety plus the lexical <template> nesting html5lib cannot preserve.

    html5lib 1.1 has no <template> support: it never pushes the
    active-formatting-elements marker HTML5 requires, so the anchor adoption
    agency can hoist a template-local anchor out of its template and into
    rendered content, where the tree no longer shows it was template content.
    That lexical nesting - which <template> tokens enclose which anchor - is the
    single fact the tree cannot be asked for, so it is the only thing inferred
    here.

    Everything else about the parse is read back off html5lib's tree instead of
    re-derived: above all *which namespace* a <template> lands in, which depends
    on integration points, annotation-xml encodings, MathML child exceptions,
    breakout tags, special-element scope barriers and implied end tags. html5lib
    already resolves all of that, so this scanner deliberately knows nothing
    about it and cannot drift into being a second tree builder.

    The one exception is the foreign-root stack, which exists only because two
    tokenizer-level facts have no tree to read them from: a </svg> or </math>
    also closes the templates opened inside it, and inside foreign content the
    text-only HTML elements hold markup rather than text. Both are needed to
    keep the raw token sequence aligned with the tree.
    """

    def __init__(self, html_template_tokens: frozenset[int] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.unsafe_hrefs: set[str] = set()
        # href -> one entry per anchor occurrence in source order, each the set of
        # <template> token indices enclosing it. Which of those tokens are HTML
        # templates is decided later, from the tree.
        self.anchor_occurrences: dict[str, list[frozenset[int]]] = {}
        self.template_token_count = 0
        # Source offset of each <template> start tag, so each token can be given
        # an identity html5lib preserves through foster parenting.
        self.template_spans: list[int] = []
        self._html_template_tokens = html_template_tokens
        self._open_templates: list[int] = []
        # (name, templates open when this root was opened). Used for the tokenizer
        # question below and, with the tree's answer, to retire foreign templates.
        # It never decides a template's namespace.
        self._foreign_roots: list[tuple[str, int]] = []
        self._line_starts = [0]

    def _record_anchor(self, attrs: list[tuple[str, str | None]]) -> None:
        hrefs = [value for key, value in attrs if key.lower() == "href" and value is not None]
        raw_start_tag = self.get_starttag_text() or ""
        if len(hrefs) != 1 or not _raw_href_is_safe(raw_start_tag, hrefs[0] if hrefs else None):
            self.unsafe_hrefs.update(hrefs)
        enclosing = frozenset(self._open_templates)
        for href in hrefs:
            self.anchor_occurrences.setdefault(href, []).append(enclosing)

    def feed(self, data: str) -> None:
        for index, character in enumerate(data):
            if character == "\n":
                self._line_starts.append(index + 1)
        super().feed(data)

    def _source_offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _open_element(self, lowered: str, self_closing: bool) -> None:
        if lowered in _FOREIGN_ROOT_TAGS:
            # A foreign element acknowledges the self-closing flag.
            if not self_closing:
                self._foreign_roots.append((lowered, len(self._open_templates)))
        elif lowered == "template":
            # Opened unconditionally. A foreign <template> is tracked too, so its
            # own end tag closes it rather than an enclosing HTML template; the
            # tree decides which of the two this token turned out to be.
            self.template_spans.append(self._source_offset())
            self._open_templates.append(self.template_token_count)
            self.template_token_count += 1

    def _retire_foreign_templates(self, depth: int) -> None:
        """Closing a foreign root also closes the foreign templates it held open.

        A token the tree proved to be an HTML template is never retired here. A
        foreign root can be stale - html5lib pops it at a breakout this scanner
        deliberately does not model - and a stale root must never be able to
        release an HTML template's suppression scope. Leaving such a token open
        can only over-suppress, which is the safe direction.
        """

        while len(self._open_templates) > depth:
            token = self._open_templates[-1]
            if self._html_template_tokens is None or token in self._html_template_tokens:
                return
            self._open_templates.pop()

    def set_cdata_mode(self, elem: str, **kwargs) -> None:
        # script/style/title/textarea and friends are text-only in HTML, but as
        # foreign elements their children are markup. Staying in normal mode
        # inside foreign content keeps anchors from going unrecorded, which would
        # otherwise let an occurrence slip past the match on the fast path.
        if self._foreign_roots:
            return
        super().set_cdata_mode(elem, **kwargs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        self._open_element(lowered, self_closing=False)
        if lowered == "a":
            self._record_anchor(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        self._open_element(lowered, self_closing=True)
        if lowered == "a":
            self._record_anchor(attrs)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _FOREIGN_ROOT_TAGS:
            for index in range(len(self._foreign_roots) - 1, -1, -1):
                name, template_depth = self._foreign_roots[index]
                if name == lowered:
                    del self._foreign_roots[index:]
                    self._retire_foreign_templates(template_depth)
                    return
            # No matching opener: HTML5 ignores the token, and so do we.
        elif lowered == "template" and self._open_templates:
            # Close the innermost open template token. With nothing open it
            # releases nothing at all.
            self._open_templates.pop()


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


def _instrument_template_tokens(html_text: str, spans: list[int]) -> tuple[str, str] | None:
    """Give every raw <template> start tag an identity that survives reparenting.

    Tree preorder is not an identity: foster parenting can move a later template
    ahead of an earlier one while the counts still match, so position alone would
    map a token to the wrong element. A marker attribute travels with the element
    instead, so a token can be matched to exactly its own element wherever
    html5lib puts it.

    Returns the instrumented source and the marker's attribute name, or None when
    instrumentation cannot be trusted - the caller then fails closed. The marker
    is never read from the page: a source that spells the reserved prefix at all
    is refused, and the name carries a per-parse nonce on top of that, so a page
    cannot forge or predict one. Only <template> start tags are touched, so raw
    href safety - which is analysed on the original source anyway - is unaffected.
    """

    if _TEMPLATE_MARKER_PREFIX in html_text.lower():
        return None

    marker = f"{_TEMPLATE_MARKER_PREFIX}{secrets.token_hex(8)}"
    pieces: list[str] = []
    previous = 0
    for token, offset in enumerate(spans):
        opening = offset + len("<template")
        if html_text[offset:opening].lower() != "<template":
            return None
        pieces.append(html_text[previous:opening])
        pieces.append(f' {marker}="{token}"')
        previous = opening
    pieces.append(html_text[previous:])
    return "".join(pieces), marker


def _html_template_token_indices(
    root,
    template_token_count: int,
    marker: str | None,
) -> frozenset[int]:
    """Ask the tree which <template> tokens turned out to be HTML templates.

    Whether a <template> carries HTML template semantics depends on the whole of
    foreign-content tree construction - integration points, the exact
    annotation-xml encoding, the MathML mglyph/malignmark child exceptions,
    breakout start tags, special-element scope barriers and implied end tags.
    html5lib resolves all of it, so the namespaces are read back off the tree in
    document order rather than re-derived from the raw source.

    When the tree does not hold one element per token - html5lib dropped a
    <template> in an insertion mode that ignores it - the tokens cannot be
    matched, so every one of them is treated as an HTML template. That fails
    closed: hidden template evidence stays suppressed.
    """

    everything_html = frozenset(range(template_token_count))
    if marker is None:
        return everything_html

    namespaces: dict[int, str | None] = {}
    for element in root.iter():
        namespace, local = _element_name(element.tag)
        if local != "template":
            continue
        values = [
            value
            for key, value in element.attrib.items()
            if str(key).lower().split("}")[-1] == marker
        ]
        if len(values) != 1:
            return everything_html
        try:
            token = int(values[0])
        except (TypeError, ValueError):
            return everything_html
        if token in namespaces or not 0 <= token < template_token_count:
            return everything_html
        namespaces[token] = namespace
    if len(namespaces) != template_token_count:
        return everything_html
    return frozenset(
        token for token, namespace in namespaces.items() if namespace == _HTML_NAMESPACE
    )


def _scan_and_parse(html_text: str):
    """Resolve the raw source against html5lib's tree, in that order.

    A first pass locates the <template> start tags so they can be marked; the
    marked source is parsed, and the tree answers which tokens are HTML
    templates. The second pass then knows enough to retire a foreign template at
    its foreign root without ever retiring an HTML one.
    """

    probe = _RawTemplateAndHrefScanner()
    probe.feed(html_text)
    probe.close()

    instrumented = _instrument_template_tokens(html_text, probe.template_spans)
    source, marker = instrumented if instrumented is not None else (html_text, None)
    fragment = html5lib.parseFragment(
        _preserve_invalid_scalars_as_control(source),
        treebuilder="etree",
        namespaceHTMLElements=True,
    )
    html_templates = _html_template_token_indices(
        fragment, probe.template_token_count, marker
    )

    scanner = _RawTemplateAndHrefScanner(html_template_tokens=html_templates)
    scanner.feed(html_text)
    scanner.close()
    return scanner, fragment, html_templates


def _template_local_occurrence_flags(html_text: str) -> dict[str, list[bool]]:
    """Resolved template-local classification per href, in source order.

    The scanner alone cannot answer this any more - it records which <template>
    tokens enclose each anchor, and the tree says which of those tokens are HTML
    templates. This seam exposes the combined answer, which is what suppression
    actually acts on.
    """

    scanner, _fragment, html_templates = _scan_and_parse(html_text)
    return {
        href: [bool(enclosing & html_templates) for enclosing in occurrences]
        for href, occurrences in scanner.anchor_occurrences.items()
    }


def _template_local_tree_anchors(root, safety_scanner, html_templates: frozenset[int]) -> set:
    """Match each tree anchor back to the raw occurrence it came from.

    Both sequences are in document order and count every anchor in any
    namespace, so a foreign anchor cannot consume an index belonging to an HTML
    one. When their lengths disagree - html5lib cloned an anchor through the
    adoption agency, or dropped one the scanner still saw - no occurrence can be
    tied to its source spelling, so the whole href fails closed.
    """

    template_hrefs = {
        href
        for href, occurrences in safety_scanner.anchor_occurrences.items()
        if any(enclosing & html_templates for enclosing in occurrences)
    }
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
            anchor
            for anchor, enclosing in zip(anchors, occurrences)
            if enclosing & html_templates
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
    safety_scanner, fragment, html_templates = _scan_and_parse(html_text)
    template_local_anchors = _template_local_tree_anchors(
        fragment, safety_scanner, html_templates
    )

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
