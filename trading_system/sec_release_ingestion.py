from __future__ import annotations

import io
import json
import os
import re
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from pypdf import PdfReader

from trading_system.release_ingestion import ReleaseDocument


class _SecIndexDocumentParser(HTMLParser):
    """Extract SEC filing documents from authoritative index columns only."""

    def __init__(self) -> None:
        super().__init__()
        self.documents: list[tuple[str, str, str]] = []
        self._in_row = False
        self._in_cell = False
        self._in_link = False
        self._cell_tag: str | None = None
        self._cells: list[tuple[str, str | None, str, str]] = []
        self._cell_parts: list[str] = []
        self._cell_href: str | None = None
        self._link_parts: list[str] = []
        self._document_index: int | None = None
        self._type_index: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "tr":
            self._in_row = True
            self._cells = []
            return
        if lowered in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._cell_tag = lowered
            self._cell_parts = []
            self._cell_href = None
            self._link_parts = []
            return
        if lowered == "a" and self._in_cell:
            values = dict(attrs)
            href = values.get("href")
            if href and self._cell_href is None:
                self._cell_href = href
                self._link_parts.extend(
                    [values.get("title") or "", values.get("aria-label") or ""]
                )
            self._in_link = True

    def handle_data(self, data: str) -> None:
        if not self._in_cell:
            return
        self._cell_parts.append(data)
        if self._in_link:
            self._link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "a":
            self._in_link = False
            return
        if lowered in {"td", "th"} and self._in_cell:
            text = " ".join(" ".join(self._cell_parts).split())
            link_text = " ".join(" ".join(self._link_parts).split())
            self._cells.append(
                (text, self._cell_href, link_text, self._cell_tag or lowered)
            )
            self._in_cell = False
            self._in_link = False
            self._cell_tag = None
            self._cell_parts = []
            self._cell_href = None
            self._link_parts = []
            return
        if lowered != "tr" or not self._in_row:
            return

        normalized = [cell[0].strip().upper() for cell in self._cells]
        is_header = any(cell[3] == "th" for cell in self._cells)
        if is_header:
            try:
                self._document_index = normalized.index("DOCUMENT")
                self._type_index = normalized.index("TYPE")
            except ValueError:
                self._document_index = None
                self._type_index = None
        elif self._document_index is not None and self._type_index is not None:
            highest = max(self._document_index, self._type_index)
            if highest < len(self._cells):
                type_text = self._cells[self._type_index][0].strip().upper()
                document_cell = self._cells[self._document_index]
                href = document_cell[1]
                if type_text == "EX-99.1" and href:
                    label = document_cell[2] or document_cell[0] or href
                    self.documents.append((href, label, type_text))

        self._in_row = False
        self._cells = []


class _SecVisibleTextParser(HTMLParser):
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
        if self._ignored_depth:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


class SecEdgarResultsProvider:
    """Discover a US earnings release from an official SEC 8-K filing.

    Discovery is intentionally conservative. The provider resolves the exact
    ticker to one CIK, considers only 8-K filings whose filingDate equals the
    calendar event's scheduled date, verifies an earnings/results signal in the
    primary filing, and then selects only a document whose SEC filing-index Type
    column is explicitly EX-99.1. It never falls back to a nearby filing date or
    an unrelated exhibit merely because one exists.
    """

    name = "sec_edgar_8k"
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
    ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/"
    MIN_DOCUMENT_CHARS = 500

    _EARNINGS_SIGNAL_RE = re.compile(
        r"(?:item\s+2\.02|results\s+of\s+operations\s+and\s+financial\s+condition|"
        r"earnings\s+release|financial\s+results|quarterly\s+results)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        ticker: str,
        scheduled_date: date,
        timeout_seconds: float = 15.0,
        user_agent: str | None = None,
    ) -> None:
        normalized = ticker.strip().upper()
        if not normalized:
            raise ValueError("SEC ticker is required")
        self.ticker = normalized
        self.scheduled_date = scheduled_date
        self.timeout_seconds = timeout_seconds
        self.user_agent = (
            user_agent
            or os.environ.get("MARKETAI_SEC_USER_AGENT")
            or "MarketAI/0.1 market-memory-bot"
        ).strip()
        if not self.user_agent:
            raise ValueError("SEC user agent is required")

    def discover(self, event_id: str) -> ReleaseDocument | None:
        cik = self._resolve_cik()
        submissions = self._fetch_json(self.SUBMISSIONS_URL.format(cik10=f"{cik:010d}"))
        filings = self._matching_filings(submissions)
        for filing in filings:
            document = self._discover_from_filing(event_id, cik, filing)
            if document is not None:
                return document
        return None

    def _resolve_cik(self) -> int:
        payload = self._fetch_json(self.COMPANY_TICKERS_URL)
        if not isinstance(payload, dict):
            raise RuntimeError("SEC company ticker response has unexpected shape")

        accepted_tickers = {self.ticker, self.ticker.replace(".", "-")}
        matches: list[int] = []
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker not in accepted_tickers:
                continue
            try:
                matches.append(int(row["cik_str"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("SEC ticker row has invalid CIK") from exc

        unique = sorted(set(matches))
        if len(unique) != 1:
            raise RuntimeError(
                f"SEC ticker {self.ticker} did not resolve to exactly one CIK"
            )
        return unique[0]

    def _matching_filings(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        recent = ((payload.get("filings") or {}).get("recent") or {})
        if not isinstance(recent, dict):
            raise RuntimeError("SEC submissions response has no recent filings")

        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        primary_documents = recent.get("primaryDocument") or []
        acceptance_times = recent.get("acceptanceDateTime") or []
        count = min(
            len(forms),
            len(filing_dates),
            len(accessions),
            len(primary_documents),
        )
        target = self.scheduled_date.isoformat()
        matches: list[dict[str, str]] = []
        for index in range(count):
            if str(forms[index]).upper() != "8-K":
                continue
            if str(filing_dates[index]) != target:
                continue
            accession = str(accessions[index]).strip()
            primary = str(primary_documents[index]).strip()
            if not accession or not primary:
                continue
            accepted = (
                str(acceptance_times[index])
                if index < len(acceptance_times)
                else ""
            )
            matches.append(
                {
                    "accession": accession,
                    "primary_document": primary,
                    "accepted": accepted,
                }
            )
        return sorted(matches, key=lambda row: row["accepted"], reverse=True)

    def _discover_from_filing(
        self,
        event_id: str,
        cik: int,
        filing: dict[str, str],
    ) -> ReleaseDocument | None:
        accession = filing["accession"]
        accession_compact = accession.replace("-", "")
        filing_base = f"{self.ARCHIVES_BASE}{cik}/{accession_compact}/"
        primary_url = urljoin(filing_base, filing["primary_document"])
        primary_html = self._fetch_text(primary_url)
        primary_text = self._html_to_text(primary_html)
        if not self._EARNINGS_SIGNAL_RE.search(primary_text):
            return None

        index_url = urljoin(filing_base, f"{accession}-index.html")
        index_html = self._fetch_text(index_url)
        parser = _SecIndexDocumentParser()
        parser.feed(index_html)

        candidates: list[tuple[str, str]] = []
        for href, label, document_type in parser.documents:
            source_url = urljoin(index_url, href)
            if not self._same_filing_directory(filing_base, source_url):
                continue
            title = " ".join(part for part in (document_type, label) if part).strip()
            candidates.append((source_url, title))

        retrieval_errors: list[Exception] = []
        for source_url, title in candidates:
            try:
                raw_text, source_type = self._fetch_release_text(source_url)
            except Exception as exc:
                retrieval_errors.append(exc)
                continue
            if len(raw_text) < self.MIN_DOCUMENT_CHARS:
                continue
            if not self._EARNINGS_SIGNAL_RE.search(raw_text):
                # Even an actual EX-99.1 can be unrelated to results. Require
                # the exhibit itself to independently carry an earnings signal.
                continue
            return ReleaseDocument(
                event_id=event_id,
                source_type=source_type,
                source_url=source_url,
                source_title=title,
                raw_text=raw_text,
            )

        # Any unevaluated qualifying EX-99.1 means we cannot safely conclude
        # that the filing has no earnings release. Retry instead of allowing a
        # successfully fetched but unrelated sibling exhibit to mask the error.
        if retrieval_errors:
            first = retrieval_errors[0]
            raise RuntimeError(
                f"SEC EX-99.1 retrieval failed for {len(retrieval_errors)} candidate(s): {first}"
            ) from first
        return None

    @staticmethod
    def _same_filing_directory(filing_base: str, candidate_url: str) -> bool:
        base = urlparse(filing_base)
        candidate = urlparse(candidate_url)
        return (
            candidate.scheme == "https"
            and candidate.netloc == "www.sec.gov"
            and candidate.path.startswith(base.path)
        )

    def _fetch_release_text(self, url: str) -> tuple[str, str]:
        data, content_type, charset = self._fetch_bytes(url)
        path = url.split("?", 1)[0].lower()
        if path.endswith(".pdf") or "pdf" in content_type.lower():
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text.strip(), "company_results_pdf"
        html = data.decode(charset, errors="replace")
        return self._html_to_text(html).strip(), "company_results"

    @staticmethod
    def _html_to_text(html: str) -> str:
        parser = _SecVisibleTextParser()
        parser.feed(html)
        return "\n".join(parser.parts)

    def _fetch_json(self, url: str) -> dict[str, Any]:
        data, _content_type, charset = self._fetch_bytes(url)
        try:
            payload = json.loads(data.decode(charset, errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("SEC returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("SEC returned unexpected JSON shape")
        return payload

    def _fetch_text(self, url: str) -> str:
        data, _content_type, charset = self._fetch_bytes(url)
        return data.decode(charset, errors="replace")

    def _fetch_bytes(self, url: str) -> tuple[bytes, str, str]:
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json,text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "identity",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "") or ""
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(), content_type, charset
