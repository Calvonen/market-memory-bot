from __future__ import annotations

import unittest
from unittest.mock import patch

from trading_system.manual_release_ingestion import ManualOfficialReleaseProvider
from trading_system.official_release_source_repository import OfficialReleaseSource


class _Provider(ManualOfficialReleaseProvider):
    def __init__(self, source: OfficialReleaseSource, payload: bytes, content_type: str = "text/html") -> None:
        super().__init__(source)
        self.payload = payload
        self.content_type = content_type
        self.fetches: list[str] = []

    def _fetch_bytes(self, url: str) -> tuple[bytes, str, str]:
        self.fetches.append(url)
        return self.payload, self.content_type, "utf-8"


class ManualOfficialReleaseProviderTests(unittest.TestCase):
    EVENT_ID = "calendar:22648076-6e43-40fc-ac6e-f57a79ceee31"

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

    def test_results_page_does_not_guess_a_release_link(self) -> None:
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "results_page",
            "https://investor.example.com/results",
            version=1,
        )
        provider = _Provider(source, b"unused")

        self.assertIsNone(provider.discover(self.EVENT_ID))
        self.assertEqual(provider.fetches, [])

    def test_event_identity_mismatch_fails_closed_before_fetch(self) -> None:
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "direct_url",
            "https://investor.example.com/results",
            version=1,
        )
        provider = _Provider(source, b"unused")

        with self.assertRaisesRegex(ValueError, "event_id mismatch"):
            provider.discover("calendar:different")
        self.assertEqual(provider.fetches, [])

    def test_short_or_empty_document_is_not_accepted(self) -> None:
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "direct_url",
            "https://investor.example.com/results",
            version=1,
        )
        provider = _Provider(source, b"<html><body>Not the release</body></html>")

        self.assertIsNone(provider.discover(self.EVENT_ID))

    def test_pdf_signature_detects_extensionless_octet_stream(self) -> None:
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "direct_url",
            "https://investor.example.com/download?id=2026",
            version=1,
        )
        provider = _Provider(source, b"%PDF-1.7\nmock-pdf", "application/octet-stream")

        with patch.object(
            ManualOfficialReleaseProvider,
            "_extract_pdf_text",
            return_value="Results " * 80,
        ) as extract:
            document = provider.discover(self.EVENT_ID)

        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.source_type, "company_results_pdf")
        extract.assert_called_once_with(b"%PDF-1.7\nmock-pdf")

    def test_pdf_extraction_failure_is_reported_as_error(self) -> None:
        source = OfficialReleaseSource(
            self.EVENT_ID,
            "direct_url",
            "https://investor.example.com/results.pdf",
            version=1,
        )
        provider = _Provider(source, b"%PDF-1.7\nbroken", "application/pdf")

        with patch(
            "trading_system.manual_release_ingestion.PdfReader",
            side_effect=ValueError("broken pdf"),
        ):
            with self.assertRaisesRegex(RuntimeError, "PDF extraction failed"):
                provider.discover(self.EVENT_ID)


if __name__ == "__main__":
    unittest.main()
