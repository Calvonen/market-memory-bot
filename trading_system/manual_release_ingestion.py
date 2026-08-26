from __future__ import annotations

import io
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from pypdf import PdfReader

from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.release_ingestion import ReleaseDocument


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


class ManualOfficialReleaseProvider:
    """Fetch exactly one user-approved official release document.

    This provider intentionally supports only ``direct_url`` sources. A
    ``results_page`` source is a discovery hint rather than a release document,
    and automatically choosing a link from such a page belongs in a separate
    provider with its own identity rules. Returning ``None`` for that kind keeps
    this first manual path fail-closed and prevents generic link guessing.
    """

    name = "manual_official_release"
    MIN_DOCUMENT_CHARS = 500

    def __init__(
        self,
        source: OfficialReleaseSource,
        *,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.source = source
        self.timeout_seconds = timeout_seconds

    def discover(self, event_id: str) -> ReleaseDocument | None:
        if event_id != self.source.event_id:
            raise ValueError("manual official release source event_id mismatch")
        if self.source.source_kind != "direct_url":
            return None

        data, content_type, charset = self._fetch_bytes(self.source.source_url)
        if self._looks_like_pdf(self.source.source_url, content_type, data):
            raw_text = self._extract_pdf_text(data)
            source_type = "company_results_pdf"
        else:
            html_text = data.decode(charset, errors="replace")
            parser = _VisibleTextParser()
            parser.feed(html_text)
            raw_text = "\n".join(parser.parts)
            source_type = "company_results"

        raw_text = raw_text.strip()
        if len(raw_text) < self.MIN_DOCUMENT_CHARS:
            return None

        return ReleaseDocument(
            event_id=event_id,
            source_type=source_type,
            source_url=self.source.source_url,
            source_title=self.source.source_title or "manual-official-release",
            raw_text=raw_text,
        )

    @staticmethod
    def _looks_like_pdf(url: str, content_type: str, data: bytes) -> bool:
        path = url.split("?", 1)[0].split("#", 1)[0]
        return (
            path.lower().endswith(".pdf")
            or "pdf" in content_type.lower()
            or data.lstrip().startswith(b"%PDF-")
        )

    @staticmethod
    def _extract_pdf_text(data: bytes) -> str:
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            raise RuntimeError("manual official release PDF extraction failed") from exc
        return "\n".join(pages)

    def _fetch_bytes(self, url: str) -> tuple[bytes, str, str]:
        request = Request(
            url,
            headers={
                "User-Agent": "MarketAI/0.1 manual-official-release-monitor",
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "") or ""
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(), content_type, charset
