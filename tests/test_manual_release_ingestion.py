from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from trading_system.manual_release_ingestion import (
    ManualOfficialReleaseProvider,
    _ApprovedOriginRedirectHandler,
)
from trading_system.official_release_source_repository import OfficialReleaseSource


class _Provider(ManualOfficialReleaseProvider):
    def __init__(self, source: OfficialReleaseSource, payload: bytes, content_type: str = "text/html") -> None:
        super().__init__(source)
        self.payload = payload
        self.content_type = content_type
        self.fetches: list[str] = []

    def _fetch_bytes(self, url: str) -> tuple[bytes, str, str | None]:
        self.fetches.append(url)
        return self.payload, self.content_type, "utf-8"


class ManualOfficialReleaseProviderTests(unittest.TestCase):
    EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"

    def _source(self, url: str = "https://investor.example.com/results") -> OfficialReleaseSource:
        return OfficialReleaseSource(self.EVENT_ID, "direct_url", url, version=1)

    def test_direct_html_source_returns_release_document(self) -> None:
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "direct_url",
            "https://investor.example.com/results",
            "FY2026 results",
            version=2,
        )
        body = "<html><body><h1>FY2026 results</h1><p>" + ("Revenue increased. " * 40) + "</p><script>ignore me</script></body></html>"
        provider = _Provider(source, body.encode("utf-8"))

        document = provider.discover(self.EVENT_ID)

        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.event_id, self.EVENT_ID)
        self.assertEqual(document.source_type, "company_results")
        self.assertEqual(document.source_url, source.source_url)
        self.assertEqual(document.source_title, "FY2026 results")
        self.assertNotIn("ignore me", document.raw_text)
        self.assertEqual(provider.fetches, [source.source_url])

    def test_text_plain_preserves_angle_bracket_fragments(self) -> None:
        source = self._source("https://investor.example.com/results.txt")
        body = (("Guidance <Q1> remains strong and <guidance> is unchanged. " * 20)).encode("utf-8")
        provider = _Provider(source, body, "text/plain; charset=utf-8")

        document = provider.discover(self.EVENT_ID)

        self.assertIsNotNone(document)
        assert document is not None
        self.assertIn("<Q1>", document.raw_text)
        self.assertIn("<guidance>", document.raw_text)

    def test_html_meta_charset_is_honored_when_http_charset_missing(self) -> None:
        source = self._source()
        text = "Tulos parani – näkymä vakaa. " * 30
        payload = (
            '<html><head><meta charset="windows-1252"></head><body>'
            + text
            + "</body></html>"
        ).encode("windows-1252")
        provider = ManualOfficialReleaseProvider(source)

        with patch.object(provider, "_fetch_bytes", return_value=(payload, "text/html", None)):
            document = provider.discover(self.EVENT_ID)

        self.assertIsNotNone(document)
        assert document is not None
        self.assertIn("–", document.raw_text)
        self.assertNotIn("�", document.raw_text)

    def test_html_bom_overrides_conflicting_http_charset(self) -> None:
        source = self._source()
        text = "Tulos parani – näkymä vakaa. " * 30
        payload = b"\xef\xbb\xbf" + (
            "<html><body>" + text + "</body></html>"
        ).encode("utf-8")
        provider = ManualOfficialReleaseProvider(source)

        with patch.object(
            provider,
            "_fetch_bytes",
            return_value=(payload, "text/html", "windows-1252"),
        ):
            document = provider.discover(self.EVENT_ID)

        self.assertIsNotNone(document)
        assert document is not None
        self.assertIn("–", document.raw_text)
        self.assertNotIn("ï»¿", document.raw_text)

    def test_commented_meta_charset_is_ignored(self) -> None:
        source = self._source()
        text = "Tulos parani – näkymä vakaa. " * 30
        payload = (
            '<html><head><!-- <meta charset="windows-1252"> -->'
            '<meta charset="utf-8"></head><body>' + text + "</body></html>"
        ).encode("utf-8")
        provider = ManualOfficialReleaseProvider(source)

        with patch.object(provider, "_fetch_bytes", return_value=(payload, "text/html", None)):
            document = provider.discover(self.EVENT_ID)

        self.assertIsNotNone(document)
        assert document is not None
        self.assertIn("–", document.raw_text)

    def test_xhtml_xml_encoding_declaration_is_honored(self) -> None:
        source = self._source()
        text = "Tulos parani – näkymä vakaa. " * 30
        payload = (
            '<?xml version="1.0" encoding="windows-1252"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>' + text + "</body></html>"
        ).encode("windows-1252")
        provider = ManualOfficialReleaseProvider(source)

        with patch.object(provider, "_fetch_bytes", return_value=(payload, "application/xhtml+xml", None)):
            document = provider.discover(self.EVENT_ID)

        self.assertIsNotNone(document)
        assert document is not None
        self.assertIn("–", document.raw_text)

    def test_results_page_does_not_guess_a_release_link(self) -> None:
        source = OfficialReleaseSource(self.EVENT_ID, "results_page", "https://investor.example.com/results", version=1)
        provider = _Provider(source, b"unused")
        self.assertIsNone(provider.discover(self.EVENT_ID))
        self.assertEqual(provider.fetches, [])

    def test_event_identity_mismatch_fails_closed_before_fetch(self) -> None:
        source = self._source()
        provider = _Provider(source, b"unused")
        with self.assertRaisesRegex(ValueError, "event_id mismatch"):
            provider.discover("calendar:different")
        self.assertEqual(provider.fetches, [])

    def test_short_or_empty_document_is_not_accepted(self) -> None:
        source = self._source()
        provider = _Provider(source, b"<html><body>Not the release</body></html>")
        self.assertIsNone(provider.discover(self.EVENT_ID))

    def test_pdf_signature_detects_extensionless_octet_stream(self) -> None:
        source = self._source("https://investor.example.com/download?id=2026")
        provider = _Provider(source, b"%PDF-1.7\nmock-pdf", "application/octet-stream")
        with patch.object(ManualOfficialReleaseProvider, "_extract_pdf_text", return_value="Results " * 80) as extract:
            document = provider.discover(self.EVENT_ID)
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.source_type, "company_results_pdf")
        extract.assert_called_once_with(b"%PDF-1.7\nmock-pdf")

    def test_pdf_extraction_failure_is_reported_as_error(self) -> None:
        source = self._source("https://investor.example.com/results.pdf")
        provider = _Provider(source, b"%PDF-1.7\nbroken", "application/pdf")
        with patch("trading_system.manual_release_ingestion.PdfReader", side_effect=ValueError("broken pdf")):
            with self.assertRaisesRegex(RuntimeError, "PDF extraction failed"):
                provider.discover(self.EVENT_ID)

    def test_pdf_without_extractable_text_is_reported_as_error(self) -> None:
        source = self._source("https://investor.example.com/scanned-results.pdf")
        provider = _Provider(source, b"%PDF-1.7\nscanned", "application/pdf")
        page = MagicMock()
        page.extract_text.return_value = None
        reader = MagicMock()
        reader.pages = [page]
        with patch("trading_system.manual_release_ingestion.PdfReader", return_value=reader):
            with self.assertRaisesRegex(RuntimeError, "produced no text"):
                provider.discover(self.EVENT_ID)

    def test_pdf_page_limit_is_enforced_before_extraction(self) -> None:
        reader = MagicMock()
        reader.pages = [MagicMock()] * (ManualOfficialReleaseProvider.MAX_PDF_PAGES + 1)
        with patch("trading_system.manual_release_ingestion.PdfReader", return_value=reader):
            with self.assertRaisesRegex(RuntimeError, "exceeds page limit"):
                ManualOfficialReleaseProvider._extract_pdf_text_in_process(b"%PDF-1.7")
        for page in reader.pages:
            page.extract_text.assert_not_called()

    def test_pdf_extracted_text_limit_is_enforced_incrementally(self) -> None:
        first = MagicMock()
        first.extract_text.return_value = "x" * (ManualOfficialReleaseProvider.MAX_EXTRACTED_CHARS // 2 + 1)
        second = MagicMock()
        second.extract_text.return_value = "y" * (ManualOfficialReleaseProvider.MAX_EXTRACTED_CHARS // 2 + 1)
        reader = MagicMock()
        reader.pages = [first, second]
        with patch("trading_system.manual_release_ingestion.PdfReader", return_value=reader):
            with self.assertRaisesRegex(RuntimeError, "extracted text exceeds size limit"):
                ManualOfficialReleaseProvider._extract_pdf_text_in_process(b"%PDF-1.7")
        first.extract_text.assert_called_once()
        second.extract_text.assert_called_once()

    def test_pdf_extraction_process_timeout_is_contextual_failure(self) -> None:
        fake_context = MagicMock()
        recv_conn = MagicMock()
        send_conn = MagicMock()
        fake_context.Pipe.return_value = (recv_conn, send_conn)
        process = MagicMock()
        fake_context.Process.return_value = process
        recv_conn.poll.return_value = False

        with patch("trading_system.manual_release_ingestion.multiprocessing.get_context", return_value=fake_context):
            with self.assertRaisesRegex(RuntimeError, "exceeded resource limit"):
                ManualOfficialReleaseProvider._extract_pdf_text(b"%PDF-1.7")

        process.terminate.assert_called_once()
        process.join.assert_called()

    def test_binary_or_structured_payload_is_rejected(self) -> None:
        source = self._source("https://investor.example.com/download")
        for content_type, payload in (
            ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK" + b"x" * 700),
            ("image/png", b"\x89PNG" + b"x" * 700),
            ("application/json", b'{"error":"temporary"}' + b" " * 700),
        ):
            with self.subTest(content_type=content_type):
                provider = _Provider(source, payload, content_type)
                with self.assertRaisesRegex(RuntimeError, "unsupported content type"):
                    provider.discover(self.EVENT_ID)

    def test_missing_content_type_accepts_only_html_sniff(self) -> None:
        source = self._source("https://investor.example.com/download")
        html_payload = ("<html><body>" + ("Results improved. " * 40) + "</body></html>").encode("utf-8")
        provider = _Provider(source, html_payload, "")
        self.assertIsNotNone(provider.discover(self.EVENT_ID))
        binary_provider = _Provider(source, b"PK" + b"x" * 700, "")
        with self.assertRaisesRegex(RuntimeError, "unsupported content type"):
            binary_provider.discover(self.EVENT_ID)

    def test_missing_content_type_accepts_bom_prefixed_html(self) -> None:
        source = self._source("https://investor.example.com/download")
        html = "<html><body>" + ("Results improved. " * 40) + "</body></html>"
        for encoding in ("utf-8-sig", "utf-16"):
            with self.subTest(encoding=encoding):
                payload = html.encode(encoding)
                provider = ManualOfficialReleaseProvider(source)
                with patch.object(provider, "_fetch_bytes", return_value=(payload, "", None)):
                    document = provider.discover(self.EVENT_ID)
                self.assertIsNotNone(document)

    def _response(self, final_url: str, *, content_length: str | None = None) -> MagicMock:
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.geturl.return_value = final_url

        def header_get(name: str, default=None):
            if name == "Content-Type":
                return "text/html"
            if name == "Content-Length":
                return content_length
            return default

        response.headers.get.side_effect = header_get
        response.headers.get_content_charset.return_value = "utf-8"
        response.read.return_value = ("<html><body>" + ("Results improved. " * 40) + "</body></html>").encode("utf-8")
        return response

    def _discover_with_response(self, provider: ManualOfficialReleaseProvider, response: MagicMock):
        opener = MagicMock()
        opener.open.return_value = response
        with patch("trading_system.manual_release_ingestion.build_opener", return_value=opener):
            return provider.discover(self.EVENT_ID)

    def test_same_origin_https_redirect_is_allowed(self) -> None:
        source = self._source()
        provider = ManualOfficialReleaseProvider(source)
        response = self._response("https://investor.example.com:443/results/final?download=1")
        self.assertIsNotNone(self._discover_with_response(provider, response))

    def test_redirect_to_unapproved_origin_is_rejected_before_following(self) -> None:
        handler = _ApprovedOriginRedirectHandler("https://investor.example.com/results")
        request = MagicMock()
        fp = MagicMock()
        headers = MagicMock()
        for final_url in (
            "https://cdn.example.net/results.pdf",
            "http://investor.example.com/results",
            "https://investor.example.com:8443/results",
        ):
            with self.subTest(final_url=final_url):
                with self.assertRaisesRegex(RuntimeError, "left approved HTTPS origin"):
                    handler.redirect_request(request, fp, 302, "Found", headers, final_url)

    def test_download_content_length_over_limit_is_rejected(self) -> None:
        source = self._source()
        provider = ManualOfficialReleaseProvider(source)
        response = self._response(source.source_url, content_length=str(provider.MAX_DOWNLOAD_BYTES + 1))
        with self.assertRaisesRegex(RuntimeError, "exceeds size limit"):
            self._discover_with_response(provider, response)
        response.read.assert_not_called()

    def test_stream_over_limit_is_rejected_even_without_content_length(self) -> None:
        source = self._source()
        provider = ManualOfficialReleaseProvider(source)
        response = self._response(source.source_url)
        response.read.return_value = b"x" * (provider.MAX_DOWNLOAD_BYTES + 1)
        with self.assertRaisesRegex(RuntimeError, "exceeds size limit"):
            self._discover_with_response(provider, response)
        response.read.assert_called_once_with(provider.MAX_DOWNLOAD_BYTES + 1)


if __name__ == "__main__":
    unittest.main()
