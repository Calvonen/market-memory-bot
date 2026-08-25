from __future__ import annotations

import io
import json
import os
import re
import time
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from pypdf import PdfReader

from trading_system.release_ingestion import ReleaseDocument


class _SecIndexDocumentParser(HTMLParser):
    """Extract SEC filing documents from authoritative index columns only."""

    def __init__(self) -> None:
        super().__init__()
        self.documents: list[tuple[str, str, str]] = []
        self.authoritative_table_recognized = False
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
                self._link_parts.extend([values.get("title") or "", values.get("aria-label") or ""])
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
            self._cells.append((text, self._cell_href, link_text, self._cell_tag or lowered))
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
                self.authoritative_table_recognized = True
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
    """Discover a US-listed earnings release from an official SEC filing."""

    name = "sec_edgar_8k"
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_BASE = "https://data.sec.gov/submissions/"
    SUBMISSIONS_URL = SUBMISSIONS_BASE + "CIK{cik10}.json"
    ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/"
    MIN_DOCUMENT_CHARS = 500
    MIN_REQUEST_INTERVAL_SECONDS = 0.15
    _company_tickers_cache: dict[str, Any] | None = None
    _last_request_at: float | None = None
    _EARNINGS_SIGNAL_RE = re.compile(r"(?:item\s+2\.02|results\s+of\s+operations\s+and\s+financial\s+condition|earnings\s+release|financial\s+results|quarterly\s+results)", re.IGNORECASE)
    _SEC_CHALLENGE_MARKERS = (
        "sec.gov | your request originates from an undeclared automated tool",
        "your request originates from an undeclared automated tool",
        "request rate threshold exceeded",
    )
    _SUPPORTED_EARNINGS_FORMS = {"8-K", "6-K"}

    def __init__(self, *, ticker: str, scheduled_date: date, timeout_seconds: float = 15.0, user_agent: str | None = None) -> None:
        normalized = ticker.strip().upper()
        if not normalized:
            raise ValueError("SEC ticker is required")
        self.ticker = normalized
        self.scheduled_date = scheduled_date
        self.timeout_seconds = timeout_seconds
        self.user_agent = (user_agent or os.environ.get("MARKETAI_SEC_USER_AGENT") or "").strip()
        if not self.user_agent:
            raise ValueError("MARKETAI_SEC_USER_AGENT is required for SEC access")
        if "@" not in self.user_agent:
            raise ValueError("SEC user agent must include a contact email address")

    @classmethod
    def clear_company_ticker_cache(cls) -> None:
        cls._company_tickers_cache = None
        cls._last_request_at = None

    def discover(self, event_id: str) -> ReleaseDocument | None:
        cik = self._resolve_cik()
        submissions = self._fetch_json(self.SUBMISSIONS_URL.format(cik10=f"{cik:010d}"))
        filings = self._matching_filings(submissions)
        if not filings:
            filings = self._matching_historical_filings(submissions)
        for filing in filings:
            document = self._discover_from_filing(event_id, cik, filing)
            if document is not None:
                return document
        return None

    def _company_tickers(self) -> dict[str, Any]:
        cached = type(self)._company_tickers_cache
        if cached is not None:
            return cached
        payload = self._fetch_json(self.COMPANY_TICKERS_URL)
        if not isinstance(payload, dict):
            raise RuntimeError("SEC company ticker response has unexpected shape")
        type(self)._company_tickers_cache = payload
        return payload

    def _resolve_cik(self) -> int:
        payload = self._company_tickers()
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
            raise RuntimeError(f"SEC ticker {self.ticker} did not resolve to exactly one CIK")
        return unique[0]

    def _matching_filings(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        recent = ((payload.get("filings") or {}).get("recent") or {})
        if not isinstance(recent, dict):
            raise RuntimeError("SEC submissions response has no recent filings")
        return self._matching_filings_table(recent)

    def _matching_historical_filings(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        filings = payload.get("filings") or {}
        if not isinstance(filings, dict):
            raise RuntimeError("SEC submissions response has invalid filings metadata")
        files = filings.get("files") or []
        if not isinstance(files, list):
            raise RuntimeError("SEC submissions response has invalid historical files")
        target = self.scheduled_date
        matches: list[dict[str, str]] = []
        for row in files:
            if not isinstance(row, dict):
                raise RuntimeError("SEC historical submissions metadata row is invalid")
            name = str(row.get("name") or "").strip()
            filing_from_raw = str(row.get("filingFrom") or "").strip()
            filing_to_raw = str(row.get("filingTo") or "").strip()
            if not name or not filing_from_raw or not filing_to_raw:
                raise RuntimeError("SEC historical submissions metadata is incomplete")
            try:
                filing_from = date.fromisoformat(filing_from_raw)
                filing_to = date.fromisoformat(filing_to_raw)
            except ValueError as exc:
                raise RuntimeError("SEC historical submissions metadata has invalid date range") from exc
            if filing_from > filing_to:
                raise RuntimeError("SEC historical submissions metadata has reversed date range")
            if not (filing_from <= target <= filing_to):
                continue
            if "/" in name or "\\" in name or not name.lower().endswith(".json"):
                raise RuntimeError("SEC historical submissions filename is invalid")
            shard = self._fetch_json(urljoin(self.SUBMISSIONS_BASE, name))
            matches.extend(self._matching_filings_table(shard))
        return sorted(matches, key=lambda row: row["accepted"], reverse=True)

    def _matching_filings_table(self, rows: dict[str, Any]) -> list[dict[str, str]]:
        if not isinstance(rows, dict):
            raise RuntimeError("SEC filing table has unexpected shape")
        required_names = ("form", "filingDate", "accessionNumber", "primaryDocument")
        required: dict[str, list[Any]] = {}
        for name in required_names:
            value = rows.get(name)
            if not isinstance(value, list):
                raise RuntimeError(f"SEC filing table field {name} is not an array")
            required[name] = value
        lengths = {len(value) for value in required.values()}
        if len(lengths) != 1:
            raise RuntimeError("SEC filing table required arrays are misaligned")
        acceptance_times = rows.get("acceptanceDateTime", [])
        if not isinstance(acceptance_times, list):
            raise RuntimeError("SEC filing table field acceptanceDateTime is not an array")
        forms = required["form"]
        filing_dates = required["filingDate"]
        accessions = required["accessionNumber"]
        primary_documents = required["primaryDocument"]
        target = self.scheduled_date
        matches: list[dict[str, str]] = []
        for index in range(len(forms)):
            filing_form = str(forms[index]).strip().upper()
            if filing_form not in self._SUPPORTED_EARNINGS_FORMS:
                continue
            filing_date_raw = str(filing_dates[index] or "").strip()
            try:
                filing_date = date.fromisoformat(filing_date_raw)
            except ValueError as exc:
                raise RuntimeError("SEC supported filing row has invalid filingDate") from exc
            if filing_date != target:
                continue
            accession = str(accessions[index]).strip()
            primary = str(primary_documents[index]).strip()
            if not accession or not primary:
                raise RuntimeError("SEC matching filing row has empty accessionNumber or primaryDocument")
            accepted = str(acceptance_times[index]) if index < len(acceptance_times) else ""
            matches.append({"form": filing_form, "accession": accession, "primary_document": primary, "accepted": accepted})
        return sorted(matches, key=lambda row: row["accepted"], reverse=True)

    def _discover_from_filing(self, event_id: str, cik: int, filing: dict[str, str]) -> ReleaseDocument | None:
        accession = filing["accession"]
        accession_compact = accession.replace("-", "")
        filing_base = f"{self.ARCHIVES_BASE}{cik}/{accession_compact}/"
        primary_url = urljoin(filing_base, filing["primary_document"])
        if not self._same_filing_directory(filing_base, primary_url):
            raise RuntimeError("SEC primary document URL escaped the filing directory")
        primary_html = self._fetch_text(primary_url)
        self._assert_not_sec_challenge_page(primary_html, context="primary filing")
        primary_text = self._html_to_text(primary_html)
        if filing.get("form") == "8-K" and not self._EARNINGS_SIGNAL_RE.search(primary_text):
            return None
        index_url = urljoin(filing_base, f"{accession}-index.html")
        index_html = self._fetch_text(index_url)
        self._assert_not_sec_challenge_page(index_html, context="filing index")
        parser = _SecIndexDocumentParser()
        parser.feed(index_html)
        if not parser.authoritative_table_recognized:
            raise RuntimeError("SEC filing index authoritative DOCUMENT/TYPE layout was not recognized")
        candidates: list[tuple[str, str]] = []
        for href, label, document_type in parser.documents:
            source_url = urljoin(index_url, href)
            if not self._same_filing_directory(filing_base, source_url):
                raise RuntimeError("SEC EX-99.1 document URL escaped the filing directory")
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
                continue
            return ReleaseDocument(event_id=event_id, source_type=source_type, source_url=source_url, source_title=title, raw_text=raw_text)
        if retrieval_errors:
            first = retrieval_errors[0]
            raise RuntimeError(f"SEC EX-99.1 retrieval failed for {len(retrieval_errors)} candidate(s): {first}") from first
        return None

    @classmethod
    def _assert_not_sec_challenge_page(cls, html: str, *, context: str) -> None:
        lowered = html.lower()
        if any(marker in lowered for marker in cls._SEC_CHALLENGE_MARKERS):
            raise RuntimeError(f"SEC returned a challenge/access page for {context}")

    @staticmethod
    def _same_filing_directory(filing_base: str, candidate_url: str) -> bool:
        base = urlparse(filing_base)
        candidate = urlparse(candidate_url)
        return candidate.scheme == "https" and candidate.netloc == "www.sec.gov" and candidate.path.startswith(base.path)

    def _fetch_release_text(self, url: str) -> tuple[str, str]:
        data, content_type, charset = self._fetch_bytes(url)
        path = url.split("?", 1)[0].lower()
        if path.endswith(".pdf") or "pdf" in content_type.lower():
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text.strip(), "company_results_pdf"
        html = data.decode(charset, errors="replace")
        self._assert_not_sec_challenge_page(html, context="EX-99.1 exhibit")
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

    @classmethod
    def _pace_sec_request(cls) -> None:
        now = time.monotonic()
        if cls._last_request_at is not None:
            wait = cls.MIN_REQUEST_INTERVAL_SECONDS - (now - cls._last_request_at)
            if wait > 0:
                time.sleep(wait)
                now += wait
        cls._last_request_at = now

    def _fetch_bytes(self, url: str) -> tuple[bytes, str, str]:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json,text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8", "Accept-Encoding": "identity"})
        for attempt in range(2):
            type(self)._pace_sec_request()
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    content_type = response.headers.get("Content-Type", "") or ""
                    charset = response.headers.get_content_charset() or "utf-8"
                    return response.read(), content_type, charset
            except HTTPError as exc:
                if attempt == 0 and exc.code in {429, 503}:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        backoff = max(1.0, float(retry_after)) if retry_after else 1.0
                    except ValueError:
                        backoff = 1.0
                    time.sleep(backoff)
                    continue
                raise
        raise RuntimeError("SEC request retry loop exhausted")
