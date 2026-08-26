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
_RENDERED_BREAK_START_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "center", "dd", "dir", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "listing", "main", "nav", "ol", "p", "plaintext",
        "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
        "xmp",
    }
)
_RENDERED_BREAK_END_TAGS = _RENDERED_BREAK_START_TAGS - {"br", "hr"}
_NON_RENDERED_TAGS = frozenset({"script", "style", "template"})
_VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_P_IMPLIED_CLOSE_START_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "center", "dd", "details", "dialog", "dir",
        "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "listing", "main", "menu",
        "nav", "ol", "p", "plaintext", "pre", "search", "section", "summary", "table", "ul",
        "xmp",
    }
)
_IMPLIED_CLOSE_ON_START = {
    "li": frozenset({"li"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
    "thead": frozenset({"thead", "tbody", "tfoot"}),
    "tbody": frozenset({"thead", "tbody", "tfoot"}),
    "tfoot": frozenset({"thead", "tbody", "tfoot"}),
    "tr": frozenset({"tr"}),
    "th": frozenset({"th", "td"}),
    "td": frozenset({"th", "td"}),
    "option": frozenset({"option"}),
    "optgroup": frozenset({"option", "optgroup"}),
}
_BASE_SCOPE_BOUNDARIES = frozenset(
    {"applet", "caption", "html", "marquee", "object", "table", "td", "template", "th"}
)
_IMPLIED_CLOSE_SCOPE_BOUNDARIES = {
    "li": frozenset({"ol", "ul", "menu"}),
    "dt": frozenset({"dl"}),
    "dd": frozenset({"dl"}),
    "thead": frozenset({"table"}),
    "tbody": frozenset({"table"}),
    "tfoot": frozenset({"table"}),
    "tr": frozenset({"table", "tbody", "thead", "tfoot"}),
    "th": frozenset({"table", "tbody", "thead", "tfoot", "tr"}),
    "td": frozenset({"table", "tbody", "thead", "tfoot", "tr"}),
    "option": frozenset({"select", "datalist", "optgroup"}),
    "optgroup": frozenset({"select", "datalist"}),
    "p": frozenset({"button", "table", "td", "th", "template", "html"}),
}
_HTML_GENERIC_END_TAG_SPECIAL_ELEMENTS = frozenset(
    {
        "address", "applet", "area", "article", "aside", "base", "basefont", "bgsound",
        "blockquote", "body", "br", "button", "caption", "center", "col", "colgroup", "dd",
        "details", "dir", "div", "dl", "dt", "embed", "fieldset", "figcaption", "figure",
        "footer", "form", "frame", "frameset", "h1", "h2", "h3", "h4", "h5", "h6",
        "head", "header", "hgroup", "hr", "html", "iframe", "img", "input", "keygen",
        "li", "link", "listing", "main", "marquee", "menu", "meta", "nav", "noembed",
        "noframes", "noscript", "object", "ol", "p", "param", "plaintext", "pre", "script",
        "search", "section", "select", "source", "style", "summary", "table", "tbody", "td",
        "template", "textarea", "tfoot", "th", "thead", "title", "tr", "track", "ul", "wbr",
        "xmp",
    }
)
_MATHML_GENERIC_END_TAG_SPECIAL_ELEMENTS = frozenset(
    {"annotation-xml", "mi", "mn", "mo", "ms", "mtext"}
)
_SVG_GENERIC_END_TAG_SPECIAL_ELEMENTS = frozenset({"desc", "foreignobject", "title"})
_MATHML_TEXT_INTEGRATION_POINTS = frozenset({"mi", "mn", "mo", "ms", "mtext"})
_SVG_HTML_INTEGRATION_POINTS = frozenset({"desc", "foreignobject", "title"})
_FOREIGN_CONTENT_BREAKOUT_START_TAGS = frozenset(
    {
        "b", "big", "blockquote", "body", "br", "center", "code", "dd", "div", "dl", "dt",
        "em", "embed", "h1", "h2", "h3", "h4", "h5", "h6", "head", "hr", "i", "img",
        "li", "listing", "menu", "meta", "nobr", "ol", "p", "pre", "ruby", "s", "small",
        "span", "strong", "strike", "sub", "sup", "table", "tt", "u", "ul", "var",
    }
)
_NON_GENERIC_EXPLICIT_END_TAGS = frozenset(_IMPLIED_CLOSE_SCOPE_BOUNDARIES) | _HEADING_TAGS | _NON_RENDERED_TAGS | {"a"}


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
        self._open_break_tags: list[str] = []
        self._non_rendered_tags: list[str] = []
        self._open_elements: list[tuple[str, bool]] = []
        self._open_namespaces: list[str] = []
        self._annotation_xml_html_integration: list[bool] = []
        self._active_anchor_index: int | None = None
        self._formatting_anchor_index: int | None = None
        self._formatting_anchor_active = False

    def _inside_non_rendered_content(self) -> bool:
        return bool(self._non_rendered_tags)

    def _inside_hidden_content(self) -> bool:
        return any(hidden for _, hidden in self._open_elements)

    def _reset_anchor(self) -> None:
        self._href = None
        self._raw_href_safe = True
        self._aria_label = None
        self._title_attr = None
        self._text_parts = []
        self._open_break_tags = []
        self._active_anchor_index = None

    def _clear_formatting_anchor(self) -> None:
        self._formatting_anchor_active = False
        self._formatting_anchor_index = None

    def _finalize_anchor(self) -> None:
        if self._href is None:
            return
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
        self._reset_anchor()

    def _html_namespace_for_start_tag(self, tag: str) -> str:
        if tag == "math":
            return "math"
        if tag == "svg":
            return "svg"
        return "html"

    def _is_html_integration_point(self, index: int) -> bool:
        tag = self._open_elements[index][0]
        namespace = self._open_namespaces[index]
        if namespace == "svg":
            return tag in _SVG_HTML_INTEGRATION_POINTS
        return (
            namespace == "math"
            and tag == "annotation-xml"
            and self._annotation_xml_html_integration[index]
        )

    def _namespace_for_new_element(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> str:
        if not self._open_elements:
            return self._html_namespace_for_start_tag(tag)

        parent_tag = self._open_elements[-1][0]
        parent_namespace = self._open_namespaces[-1]
        if parent_namespace == "html":
            return self._html_namespace_for_start_tag(tag)
        if parent_namespace == "math":
            if parent_tag == "annotation-xml" and tag == "svg":
                return "svg"
            if parent_tag in _MATHML_TEXT_INTEGRATION_POINTS and tag not in {"mglyph", "malignmark"}:
                return self._html_namespace_for_start_tag(tag)
            if parent_tag == "annotation-xml" and self._annotation_xml_html_integration[-1]:
                return self._html_namespace_for_start_tag(tag)
            return "math"
        if parent_namespace == "svg" and parent_tag in _SVG_HTML_INTEGRATION_POINTS:
            return self._html_namespace_for_start_tag(tag)
        return parent_namespace

    def _push_element(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        hidden = any(key.lower() == "hidden" for key, _ in attrs)
        namespace = self._namespace_for_new_element(tag, attrs)
        if namespace == "html" and tag in _VOID_TAGS:
            return
        values = {key.lower(): value for key, value in attrs if value is not None}
        annotation_xml_html_integration = (
            namespace == "math"
            and tag == "annotation-xml"
            and values.get("encoding", "").lower() in {"text/html", "application/xhtml+xml"}
        )
        self._open_elements.append((tag, hidden))
        self._open_namespaces.append(namespace)
        self._annotation_xml_html_integration.append(annotation_xml_html_integration)

    def _is_suppressed_element(self, tag: str, namespace: str) -> bool:
        return tag in {"script", "style"} or (tag == "template" and namespace == "html")

    def _scope_boundaries_for(self, tag: str) -> frozenset[str]:
        return _BASE_SCOPE_BOUNDARIES | _IMPLIED_CLOSE_SCOPE_BOUNDARIES.get(tag, frozenset())

    def _is_generic_special_element(self, index: int) -> bool:
        open_tag = self._open_elements[index][0]
        namespace = self._open_namespaces[index]
        if namespace == "html":
            return open_tag in _HTML_GENERIC_END_TAG_SPECIAL_ELEMENTS
        if namespace == "math":
            return open_tag in _MATHML_GENERIC_END_TAG_SPECIAL_ELEMENTS
        if namespace == "svg":
            return open_tag in _SVG_GENERIC_END_TAG_SPECIAL_ELEMENTS
        return False

    def _find_open_element_index(self, tag: str) -> int | None:
        boundaries = self._scope_boundaries_for(tag)
        generic_close = tag not in _NON_GENERIC_EXPLICIT_END_TAGS
        for index in range(len(self._open_elements) - 1, -1, -1):
            open_tag = self._open_elements[index][0]
            if open_tag == tag:
                return index
            if open_tag in boundaries or (generic_close and self._is_generic_special_element(index)):
                return None
        return None

    def _delete_open_elements_from(self, index: int, *, clear_formatting_anchor: bool = False) -> None:
        if (
            self._active_anchor_index is not None
            and index <= self._active_anchor_index
        ):
            if clear_formatting_anchor or not self._formatting_anchor_active:
                self._finalize_anchor()
            else:
                self._active_anchor_index = None
        if self._formatting_anchor_index is not None and index <= self._formatting_anchor_index:
            self._formatting_anchor_index = None
            if clear_formatting_anchor:
                self._clear_formatting_anchor()
        removed_non_rendered = [
            tag
            for (tag, _), namespace in zip(self._open_elements[index:], self._open_namespaces[index:])
            if self._is_suppressed_element(tag, namespace)
        ]
        for tag in reversed(removed_non_rendered):
            if self._non_rendered_tags and self._non_rendered_tags[-1] == tag:
                self._non_rendered_tags.pop()
        del self._open_elements[index:]
        del self._open_namespaces[index:]
        del self._annotation_xml_html_integration[index:]

    def _pop_element(self, tag: str) -> bool:
        index = self._find_open_element_index(tag)
        if index is None:
            return False
        self._delete_open_elements_from(index)
        return True

    def _close_open_element_at(self, index: int) -> None:
        open_tag = self._open_elements[index][0]
        self._delete_open_elements_from(index)
        if open_tag in self._open_break_tags:
            break_index = len(self._open_break_tags) - 1 - self._open_break_tags[::-1].index(open_tag)
            del self._open_break_tags[break_index:]
            if self._href is not None and not self._inside_non_rendered_content():
                self._text_parts.append(" ")

    def _find_implied_close_index(self, closing_tags: frozenset[str]) -> int | None:
        boundaries: set[str] = set()
        for closing_tag in closing_tags:
            boundaries.update(_BASE_SCOPE_BOUNDARIES)
            boundaries.update(_IMPLIED_CLOSE_SCOPE_BOUNDARIES.get(closing_tag, frozenset()))
        for index in range(len(self._open_elements) - 1, -1, -1):
            open_tag = self._open_elements[index][0]
            if open_tag in closing_tags:
                return index
            if open_tag in boundaries:
                return None
        return None

    def _close_first_in_scope(self, closing_tags: frozenset[str]) -> None:
        close_index = self._find_implied_close_index(closing_tags)
        if close_index is not None:
            self._close_open_element_at(close_index)

    def _apply_implied_closes(self, incoming_tag: str) -> None:
        closing_tags = _IMPLIED_CLOSE_ON_START.get(incoming_tag, frozenset())
        if closing_tags:
            self._close_first_in_scope(closing_tags)

        if incoming_tag in _P_IMPLIED_CLOSE_START_TAGS:
            self._close_first_in_scope(frozenset({"p"}))

        if (
            incoming_tag in _HEADING_TAGS
            and self._open_elements
            and self._open_elements[-1][0] in _HEADING_TAGS
        ):
            self._close_open_element_at(len(self._open_elements) - 1)

    def _is_foreign_content_breakout(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in _FOREIGN_CONTENT_BREAKOUT_START_TAGS:
            return True
        if tag != "font":
            return False
        attr_names = {key.lower() for key, _ in attrs}
        return bool(attr_names & {"color", "face", "size"})

    def _current_node_is_foreign_without_integration(self) -> bool:
        if not self._open_elements:
            return False
        index = len(self._open_elements) - 1
        if self._open_namespaces[index] == "html" or self._is_html_integration_point(index):
            return False
        return not (
            self._open_namespaces[index] == "math"
            and self._open_elements[index][0] in _MATHML_TEXT_INTEGRATION_POINTS
        )

    def _current_node_is_foreign(self) -> bool:
        return bool(self._open_elements and self._open_namespaces[-1] != "html")

    def _exit_foreign_content_for_breakout(self) -> None:
        while self._open_elements:
            index = len(self._open_elements) - 1
            if self._open_namespaces[index] == "html" or self._is_html_integration_point(index):
                return
            if (
                self._open_namespaces[index] == "math"
                and self._open_elements[index][0] in _MATHML_TEXT_INTEGRATION_POINTS
            ):
                return
            self._delete_open_elements_from(index)

    def _exit_foreign_content_for_endtag_breakout(self) -> None:
        while self._open_elements and self._open_namespaces[-1] != "html":
            self._delete_open_elements_from(len(self._open_elements) - 1)

    def _handle_foreign_endtag(self, tag: str) -> None:
        for index in range(len(self._open_elements) - 1, -1, -1):
            if self._open_namespaces[index] == "html":
                return
            if self._open_elements[index][0] == tag:
                self._delete_open_elements_from(index)
                return

    def _append_visible_break(self) -> None:
        if (
            self._href is not None
            and not self._inside_non_rendered_content()
            and not self._inside_hidden_content()
        ):
            self._text_parts.append(" ")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if self._current_node_is_foreign_without_integration() and self._is_foreign_content_breakout(normalized_tag, attrs):
            self._exit_foreign_content_for_breakout()

        incoming_namespace = self._namespace_for_new_element(normalized_tag, attrs)
        if incoming_namespace == "html":
            self._apply_implied_closes(normalized_tag)
            incoming_namespace = self._namespace_for_new_element(normalized_tag, attrs)

        ancestor_hidden = self._inside_hidden_content()
        ancestor_non_rendered = self._inside_non_rendered_content()

        if (
            normalized_tag == "a"
            and incoming_namespace == "html"
            and self._formatting_anchor_active
            and not ancestor_non_rendered
        ):
            formatting_anchor_index = self._formatting_anchor_index
            self._finalize_anchor()
            if formatting_anchor_index is not None and formatting_anchor_index < len(self._open_elements):
                self._delete_open_elements_from(formatting_anchor_index, clear_formatting_anchor=True)
            else:
                self._clear_formatting_anchor()
            ancestor_hidden = self._inside_hidden_content()
            incoming_namespace = self._namespace_for_new_element(normalized_tag, attrs)

        self._push_element(normalized_tag, attrs)

        if self._is_suppressed_element(normalized_tag, incoming_namespace):
            self._non_rendered_tags.append(normalized_tag)
            return

        if normalized_tag == "a":
            if incoming_namespace != "html":
                return
            if ancestor_non_rendered:
                return
            self._formatting_anchor_active = True
            self._formatting_anchor_index = len(self._open_elements) - 1
            if ancestor_hidden or self._inside_hidden_content():
                self._reset_anchor()
                return
            values = {key.lower(): value for key, value in attrs if value is not None}
            self._href = values.get("href")
            self._raw_href_safe = _raw_href_is_safe(self.get_starttag_text(), self._href)
            self._aria_label = values.get("aria-label")
            self._title_attr = values.get("title")
            self._text_parts = []
            self._open_break_tags = []
            self._active_anchor_index = len(self._open_elements) - 1 if self._href is not None else None
            return

        if self._href is None or ancestor_hidden or ancestor_non_rendered or self._inside_hidden_content():
            return
        if incoming_namespace == "html" and normalized_tag in _RENDERED_BREAK_START_TAGS:
            self._text_parts.append(" ")
            if normalized_tag in _RENDERED_BREAK_END_TAGS:
                self._open_break_tags.append(normalized_tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        self.handle_starttag(tag, attrs)
        if not self._open_elements:
            return
        if self._open_elements[-1][0] != normalized_tag:
            return
        namespace = self._open_namespaces[-1]
        if namespace != "html":
            self.handle_endtag(tag)
        elif normalized_tag in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if (
            self._href is not None
            and self._formatting_anchor_active
            and not self._inside_non_rendered_content()
            and not self._inside_hidden_content()
        ):
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()

        if self._current_node_is_foreign():
            if normalized_tag in {"p", "br"}:
                self._exit_foreign_content_for_endtag_breakout()
            else:
                self._handle_foreign_endtag(normalized_tag)
                return

        if normalized_tag == "br":
            self._append_visible_break()
            return

        if normalized_tag == "p" and self._find_open_element_index("p") is None:
            self._append_visible_break()
            return

        if normalized_tag in _NON_RENDERED_TAGS:
            index = self._find_open_element_index(normalized_tag)
            if index is None or not self._is_suppressed_element(normalized_tag, self._open_namespaces[index]):
                return
            if self._non_rendered_tags and self._non_rendered_tags[-1] == normalized_tag:
                self._pop_element(normalized_tag)
            return

        if normalized_tag == "a":
            anchor_index = self._find_open_element_index(normalized_tag)
            if anchor_index is not None:
                if self._active_anchor_index == anchor_index:
                    self._finalize_anchor()
                self._delete_open_elements_from(anchor_index, clear_formatting_anchor=True)
                self._clear_formatting_anchor()
                return
            if self._formatting_anchor_active:
                self._finalize_anchor()
                self._clear_formatting_anchor()
            return

        if (
            self._href is not None
            and not self._inside_non_rendered_content()
            and not self._inside_hidden_content()
            and normalized_tag in _RENDERED_BREAK_END_TAGS
        ):
            for index in range(len(self._open_break_tags) - 1, -1, -1):
                if self._open_break_tags[index] == normalized_tag:
                    del self._open_break_tags[index:]
                    self._text_parts.append(" ")
                    break
        self._pop_element(normalized_tag)

    def close(self) -> None:
        super().close()
        self._finalize_anchor()
        self._clear_formatting_anchor()


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
    """Extract deterministic same-origin HTTPS candidates from a results page."""
    if source.source_kind != "results_page":
        raise ValueError("results page candidate extraction requires source_kind=results_page")

    page_url = _canonical_candidate_url(source.source_url, source.source_url)
    if page_url is None:
        return ()

    parser = _ResultsPageLinkParser()
    parser.feed(html_text)
    parser.close()

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
