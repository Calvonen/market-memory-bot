from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin
from urllib.request import Request, urlopen


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
            self._href = dict(attrs).get("href")
            self._text = []

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
    name = "hays_results_centre"
    RESULTS_URL = "https://www.haysplc.com/investors/results-centre"
    TARGET_PHRASES = (
        "full-year results for the year ended 30 june 2026",
        "preliminary results for the year ended 30 june 2026",
        "full year results for the year ended 30 june 2026",
    )

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def discover(self, event_id: str) -> ReleaseDocument | None:
        listing_html = self._fetch(self.RESULTS_URL)
        parser = _LinkTextParser()
        parser.feed(listing_html)

        for href, title in parser.links:
            normalized = " ".join(title.lower().replace("–", "-").split())
            if any(phrase in normalized for phrase in self.TARGET_PHRASES):
                source_url = urljoin(self.RESULTS_URL, href)
                release_html = self._fetch(source_url)
                text_parser = _VisibleTextParser()
                text_parser.feed(release_html)
                raw_text = "\n".join(text_parser.parts)
                if len(raw_text) < 500:
                    continue
                return ReleaseDocument(
                    event_id=event_id,
                    source_type="company_results",
                    source_url=source_url,
                    source_title=title,
                    raw_text=raw_text,
                )
        return None

    def _fetch(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": "MarketAI/0.1 official-results-monitor",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
