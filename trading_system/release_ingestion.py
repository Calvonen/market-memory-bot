from __future__ import annotations

import io
import re
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from pypdf import PdfReader


@dataclass(frozen=True)
class ReleaseDocument:
    event_id: str
    source_type: str
    source_url: str
    source_title: str
    raw_text: str

    @property
    def content_sha256(self) -> str:
        return sha256(self.raw_text.encode("utf-8")).hexdigest()


class OfficialReleaseProvider(Protocol):
    name: str

    def discover(self, event_id: str) -> ReleaseDocument | None: ...


class _LinkTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            values = dict(attrs)
            self._href = values.get("href")
            self._text = [values.get("aria-label") or "", values.get("title") or ""]
        elif tag.lower() == "img" and self._href is not None:
            self._text.append(dict(attrs).get("alt") or "")

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            self.links.append((self._href, text))
            self._href = None
            self._text = []


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)


class HaysResultsCentreProvider:
    """Discover the official Hays FY2026 full-year/preliminary results release.

    Matching is intentionally conservative: a candidate link is only accepted
    when it carries an explicit, unambiguous FY2026 signal (either "year ended
    30 June 2026" or an "FY26"/"FY2026" token). Any explicit signal for a
    *different* year anywhere in the link text/URL disqualifies the candidate,
    even if a result-type keyword also matches, so an old-year report can never
    be picked up by accident. A link with no explicit year signal at all is
    only accepted via the bare "2026" fallback, alongside a result-type
    keyword - this keeps the widened matching from becoming a silent rubber
    stamp for unrelated pages.
    """

    name = "hays_results_centre"
    RESULTS_URL = "https://www.haysplc.com/investors/results-centre"

    MIN_DOCUMENT_CHARS = 500
    _TARGET_YEAR = "2026"
    _TARGET_FY_SHORT = "26"

    _RESULT_TYPE_RE = re.compile(r"\b(full[\s-]?year|preliminary|annual)\b")
    _RESULT_WORD_RE = re.compile(r"\bresults?\b")
    _YEAR_END_RE = re.compile(
        r"year\s+ended\s+(\d{1,2})(?:st|nd|rd|th)?\s+june\s+(\d{4})"
    )
    _FY_TOKEN_RE = re.compile(r"\bfy[\s-]?(\d{2}|\d{4})\b")

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        *,
        override_url: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        # Manual, explicit escape hatch: when set, discovery trusts this exact
        # URL instead of scanning/matching the results-centre listing page.
        # Never sourced from anything but caller-supplied config (env var or
        # CLI flag) - no secrets, no admin token involved.
        self.override_url = override_url

    def discover(self, event_id: str) -> ReleaseDocument | None:
        if self.override_url:
            return self._build_document(
                event_id, self.override_url, source_title="manual-override"
            )

        listing_html = self._fetch(self.RESULTS_URL)
        parser = _LinkTextParser()
        parser.feed(listing_html)

        candidates = [
            (self._match_priority(title, href), href, title)
            for href, title in parser.links
            if self._matches_target_release(title, href)
        ]
        for _priority, href, title in sorted(candidates, reverse=True):
            source_url = urljoin(self.RESULTS_URL, href)
            try:
                document = self._build_document(event_id, source_url, title)
            except Exception:
                # A single broken/unreachable candidate link must not abort
                # discovery of the real release elsewhere on the page.
                continue
            if document is not None:
                return document
        return None

    @classmethod
    def _normalize(cls, text: str) -> str:
        return " ".join(text.lower().replace("–", "-").replace("—", "-").split())

    @classmethod
    def _matches_target_release(cls, title: str, href: str) -> bool:
        haystack = cls._normalize(f"{title} {href}")
        if not cls._RESULT_TYPE_RE.search(haystack) or not cls._RESULT_WORD_RE.search(
            haystack
        ):
            return False

        year_end_matches = cls._YEAR_END_RE.findall(haystack)
        if year_end_matches:
            if any(
                not (day == "30" and year == cls._TARGET_YEAR)
                for day, year in year_end_matches
            ):
                return False
            return True

        fy_matches = cls._FY_TOKEN_RE.findall(haystack)
        if fy_matches:
            if any(
                token not in {cls._TARGET_FY_SHORT, cls._TARGET_YEAR}
                for token in fy_matches
            ):
                return False
            return True

        return cls._TARGET_YEAR in haystack

    @classmethod
    def _match_priority(cls, title: str, href: str) -> int:
        """Prefer the strongest explicit FY2026 signal, independent of link order."""
        haystack = cls._normalize(f"{title} {href}")
        if ("30", cls._TARGET_YEAR) in cls._YEAR_END_RE.findall(haystack):
            return 3
        if any(
            token in {cls._TARGET_FY_SHORT, cls._TARGET_YEAR}
            for token in cls._FY_TOKEN_RE.findall(haystack)
        ):
            return 2
        return 1

    def _build_document(
        self, event_id: str, source_url: str, source_title: str
    ) -> ReleaseDocument | None:
        data, content_type, charset = self._fetch_bytes(source_url)

        if self._looks_like_pdf(source_url, content_type):
            raw_text = self._extract_pdf_text(data)
            source_type = "company_results_pdf"
        else:
            html_text = data.decode(charset, errors="replace")
            text_parser = _VisibleTextParser()
            text_parser.feed(html_text)
            raw_text = "\n".join(text_parser.parts)
            source_type = "company_results"

        raw_text = raw_text.strip()
        if len(raw_text) < self.MIN_DOCUMENT_CHARS:
            return None

        return ReleaseDocument(
            event_id=event_id,
            source_type=source_type,
            source_url=source_url,
            source_title=source_title,
            raw_text=raw_text,
        )

    @staticmethod
    def _looks_like_pdf(url: str, content_type: str) -> bool:
        path = url.split("?", 1)[0].split("#", 1)[0]
        return path.lower().endswith(".pdf") or "pdf" in content_type.lower()

    @staticmethod
    def _extract_pdf_text(data: bytes) -> str:
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception:
            return ""
        return "\n".join(pages)

    def _fetch(self, url: str) -> str:
        data, _content_type, charset = self._fetch_bytes(url)
        return data.decode(charset, errors="replace")

    def _fetch_bytes(self, url: str) -> tuple[bytes, str, str]:
        request = Request(
            url,
            headers={
                "User-Agent": "MarketAI/0.1 official-results-monitor",
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "") or ""
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(), content_type, charset
