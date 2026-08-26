from __future__ import annotations

import codecs
import io
import multiprocessing
import re
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

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


class _EncodingMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.encoding: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.encoding is not None or tag.lower() != "meta":
            return
        values = {key.lower(): value for key, value in attrs if value is not None}
        charset = values.get("charset")
        if charset:
            self.encoding = charset.strip()
            return
        if values.get("http-equiv", "").lower() != "content-type":
            return
        content = values.get("content", "")
        match = re.search(r"charset\s*=\s*([A-Za-z0-9._:-]+)", content, flags=re.IGNORECASE)
        if match:
            self.encoding = match.group(1)


class _ApprovedOriginRedirectHandler(HTTPRedirectHandler):
    def __init__(self, approved_url: str) -> None:
        super().__init__()
        self.approved_url = approved_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        ManualOfficialReleaseProvider._validate_final_url(self.approved_url, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _pdf_extract_worker(
    data: bytes,
    send_conn,
    max_pages: int,
    max_chars: int,
    memory_limit_bytes: int,
    cpu_limit_seconds: int,
) -> None:  # type: ignore[no-untyped-def]
    """Run pypdf behind OS resource limits so decompression cannot exhaust the worker."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit_seconds, cpu_limit_seconds))
        text = ManualOfficialReleaseProvider._extract_pdf_text_in_process(
            data,
            max_pages=max_pages,
            max_chars=max_chars,
        )
        send_conn.send(("ok", text))
    except BaseException as exc:
        try:
            send_conn.send(("error", f"{type(exc).__name__}: {exc}"))
        except BaseException:
            pass
    finally:
        send_conn.close()


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
    MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
    MAX_PDF_PAGES = 500
    MAX_EXTRACTED_CHARS = 5_000_000
    PDF_EXTRACTION_MEMORY_BYTES = 1024 * 1024 * 1024
    PDF_EXTRACTION_CPU_SECONDS = 10
    PDF_EXTRACTION_TIMEOUT_SECONDS = 15.0

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

        data, content_type, http_charset = self._fetch_bytes(self.source.source_url)
        media_type = self._media_type(content_type)
        if self._looks_like_pdf(self.source.source_url, content_type, data):
            raw_text = self._extract_pdf_text(data)
            source_type = "company_results_pdf"
        elif media_type == "text/plain":
            raw_text = self._decode_text(data, http_charset, allow_html_meta=False)
            source_type = "company_results"
        elif self._looks_like_html_or_text(content_type, data):
            html_text = self._decode_text(
                data,
                http_charset,
                allow_html_meta=True,
                allow_xml_declaration=media_type == "application/xhtml+xml",
            )
            parser = _VisibleTextParser()
            parser.feed(html_text)
            raw_text = "\n".join(parser.parts)
            source_type = "company_results"
        else:
            raise RuntimeError(
                f"manual official release returned unsupported content type: {media_type or '<missing>'}"
            )

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
    def _media_type(content_type: str) -> str:
        return content_type.split(";", 1)[0].strip().lower()

    @staticmethod
    def _looks_like_pdf(url: str, content_type: str, data: bytes) -> bool:
        path = url.split("?", 1)[0].split("#", 1)[0]
        return (
            path.lower().endswith(".pdf")
            or "pdf" in content_type.lower()
            or data.lstrip().startswith(b"%PDF-")
        )

    @classmethod
    def _looks_like_html_or_text(cls, content_type: str, data: bytes) -> bool:
        media_type = cls._media_type(content_type)
        if media_type in {"text/html", "application/xhtml+xml", "text/plain"}:
            return True
        if media_type:
            return False

        bom_encoding = cls._sniff_bom_encoding(data)
        if bom_encoding:
            try:
                decoder = codecs.getincrementaldecoder(bom_encoding)(errors="strict")
                prefix_text = decoder.decode(data[:1024], final=False).lstrip().lower()
            except (LookupError, UnicodeDecodeError):
                return False
            return prefix_text.startswith(("<!doctype html", "<html", "<head", "<body"))

        prefix = data.lstrip()[:256].lower()
        return (
            prefix.startswith(b"<!doctype html")
            or prefix.startswith(b"<html")
            or prefix.startswith(b"<head")
            or prefix.startswith(b"<body")
        )

    @staticmethod
    def _sniff_bom_encoding(data: bytes) -> str | None:
        if data.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            return "utf-32"
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return "utf-16"
        return None

    @staticmethod
    def _sniff_html_meta_encoding(data: bytes) -> str | None:
        head = data[:4096].decode("latin-1", errors="strict")
        parser = _EncodingMetaParser()
        parser.feed(head)
        return parser.encoding

    @staticmethod
    def _sniff_xml_encoding(data: bytes) -> str | None:
        head = data[:512].decode("latin-1", errors="strict")
        match = re.match(
            r"\s*<\?xml\b[^>]*\bencoding\s*=\s*['\"]([A-Za-z0-9._:-]+)['\"]",
            head,
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else None

    @classmethod
    def _decode_text(
        cls,
        data: bytes,
        http_charset: str | None,
        *,
        allow_html_meta: bool,
        allow_xml_declaration: bool = False,
    ) -> str:
        bom_encoding = cls._sniff_bom_encoding(data)
        if allow_html_meta or allow_xml_declaration:
            encoding = (
                bom_encoding
                or http_charset
                or (cls._sniff_xml_encoding(data) if allow_xml_declaration else None)
                or (cls._sniff_html_meta_encoding(data) if allow_html_meta else None)
                or "utf-8"
            )
        else:
            encoding = http_charset or bom_encoding or "utf-8"
        try:
            return data.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"manual official release text decode failed for charset {encoding}"
            ) from exc

    @classmethod
    def _extract_pdf_text_in_process(
        cls,
        data: bytes,
        *,
        max_pages: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        page_limit = cls.MAX_PDF_PAGES if max_pages is None else max_pages
        char_limit = cls.MAX_EXTRACTED_CHARS if max_chars is None else max_chars
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > page_limit:
            raise RuntimeError("manual official release PDF exceeds page limit")
        parts: list[str] = []
        total_chars = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            total_chars += len(text)
            if total_chars > char_limit:
                raise RuntimeError("manual official release PDF extracted text exceeds size limit")
            parts.append(text)
        raw_text = "\n".join(parts).strip()
        if not raw_text:
            raise RuntimeError("manual official release PDF extraction produced no text")
        return raw_text

    @classmethod
    def _extract_pdf_text(cls, data: bytes) -> str:
        try:
            context = multiprocessing.get_context("spawn")
        except ValueError as exc:
            raise RuntimeError(
                "manual official release PDF extraction requires a spawn-capable runtime"
            ) from exc

        recv_conn, send_conn = context.Pipe(duplex=False)
        process = context.Process(
            target=_pdf_extract_worker,
            args=(
                data,
                send_conn,
                cls.MAX_PDF_PAGES,
                cls.MAX_EXTRACTED_CHARS,
                cls.PDF_EXTRACTION_MEMORY_BYTES,
                cls.PDF_EXTRACTION_CPU_SECONDS,
            ),
        )
        process.start()
        send_conn.close()
        try:
            if not recv_conn.poll(cls.PDF_EXTRACTION_TIMEOUT_SECONDS):
                process.terminate()
                process.join(timeout=1.0)
                raise RuntimeError("manual official release PDF extraction exceeded resource limit")
            status, payload = recv_conn.recv()
        except (EOFError, OSError) as exc:
            process.join(timeout=1.0)
            raise RuntimeError("manual official release PDF extraction exceeded resource limit") from exc
        finally:
            recv_conn.close()

        process.join(timeout=1.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
        if status != "ok":
            if "exceeds page limit" in payload or "extracted text exceeds size limit" in payload or "produced no text" in payload:
                raise RuntimeError(payload.split(": ", 1)[-1])
            raise RuntimeError("manual official release PDF extraction failed")
        return payload

    @staticmethod
    def _https_origin(url: str) -> tuple[str, str, int]:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").rstrip(".").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("manual official release redirect has invalid port") from exc
        effective_port = 443 if port is None else port
        return scheme, host, effective_port

    @classmethod
    def _validate_final_url(cls, approved_url: str, final_url: str) -> None:
        approved_origin = cls._https_origin(approved_url)
        final_origin = cls._https_origin(final_url)
        if (
            approved_origin[0] != "https"
            or final_origin[0] != "https"
            or final_origin != approved_origin
        ):
            raise RuntimeError(
                "manual official release redirect left approved HTTPS origin"
            )

    @classmethod
    def _read_bounded(cls, response) -> bytes:  # type: ignore[no-untyped-def]
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError):
                declared_length = None
            if declared_length is not None and declared_length > cls.MAX_DOWNLOAD_BYTES:
                raise RuntimeError("manual official release download exceeds size limit")

        data = response.read(cls.MAX_DOWNLOAD_BYTES + 1)
        if len(data) > cls.MAX_DOWNLOAD_BYTES:
            raise RuntimeError("manual official release download exceeds size limit")
        return data

    def _fetch_bytes(self, url: str) -> tuple[bytes, str, str | None]:
        request = Request(
            url,
            headers={
                "User-Agent": "MarketAI/0.1 manual-official-release-monitor",
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            },
        )
        opener = build_opener(_ApprovedOriginRedirectHandler(url))
        with opener.open(request, timeout=self.timeout_seconds) as response:
            final_url = response.geturl()
            self._validate_final_url(url, final_url)
            content_type = response.headers.get("Content-Type", "") or ""
            http_charset = response.headers.get_content_charset()
            data = self._read_bounded(response)
            return data, content_type, http_charset
