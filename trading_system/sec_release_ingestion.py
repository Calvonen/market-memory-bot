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


class _SecLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        self._href = values.get("href")
        self._parts = [values.get("title") or "", values.get("aria-label") or ""]

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = " ".join(" ".join(self._parts).split())
        self.links.append((self._href, text))
        self._href = None
        self._parts = []


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
    filing, and then selects an EX-99.1-style linked exhibit. It never falls back
    to a nearby filing date or an unrelated exhibit merely because one exists.
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
    _EXHIBIT_SIGNAL_RE = re.compile(
        r"(?:ex(?:hibit)?[\s._-]*99[\s._-]*1|99[\s._-]*1|earnings|results)",
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
        accession_compact = filing["accession"].replace("-", "")
        filing_base = f"{self.ARCHIVES_BASE}{cik}/{accession_compact}/"
        primary_url = urljoin(filing_base, filing["primary_document"])
        primary_html = self._fetch_text(primary_url)
        primary_text = self._html_to_text(primary_html)
        if not self._EARNINGS_SIGNAL_RE.search(primary_text):
            return None

        parser = _SecLinkParser()
        parser.feed(primary_html)
        candidates: list[tuple[int, str, str]] = []
        for href, label in parser.links:
            source_url = urljoin(primary_url, href)
            if not self._same_filing_directory(filing_base, source_url):
                continue
            haystack = f"{label} {href}"
            if not self._EXHIBIT_SIGNAL_RE.search(haystack):
                continue
            priority = self._exhibit_priority(haystack)
            candidates.append((priority, source_url, label or href))

        for _priority, source_url, title in sorted(candidates, reverse=True):
            try:
                raw_text, source_type = self._fetch_release_text(source_url)
            except Exception:
                continue
            if len(raw_text) < self.MIN_DOCUMENT_CHARS:
                continue
            if not self._EARNINGS_SIGNAL_RE.search(raw_text):
                # The filing itself is earnings-related, but a generic 99.1 can
                # still be an unrelated attachment. Require the exhibit text to
                # independently carry an earnings/results signal.
                continue
            return ReleaseDocument(
                event_id=event_id,
                source_type=source_type,
                source_url=source_url,
                source_title=title,
                raw_text=raw_text,
            )
        return None

    @classmethod
    def _exhibit_priority(cls, haystack: str) -> int:
        lowered = haystack.lower()
        if re.search(r"ex(?:hibit)?[\s._-]*99[\s._-]*1|99[\s._-]*1", lowered):
            return 3
        if "earnings" in lowered:
            return 2
        return 1

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
            try:
                reader = PdfReader(io.BytesIO(data))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception:
                text = ""
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
