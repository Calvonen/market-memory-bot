import unittest

from trading_system.ai_event_analyzer import EventAnalysisPayload
from trading_system.event_strategy_bridge import event_analysis_components
from trading_system.models import Direction
from trading_system.release_ingestion import HaysResultsCentreProvider


def _minimal_pdf_bytes(text: str) -> bytes:
    """Hand-build the smallest valid single-page PDF that embeds `text`.

    Used only to exercise PDF extraction in tests without a heavyweight
    PDF-writing dependency; production code only ever reads PDFs (via pypdf).
    """
    content_lines = ["BT", "/F1 12 Tf", "72 720 Td"]
    for line in text.split("\n"):
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content_lines.append(f"({escaped}) Tj")
        content_lines.append("0 -14 Td")
    content_lines.append("ET")
    content_stream = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n",
        (b"4 0 obj\n<< /Length %d >>\nstream\n" % len(content_stream)) + content_stream + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    body = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(body))
        body += obj
    xref_offset = len(body)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        xref += f"{offset:010d} 00000 n \n".encode("ascii")
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("ascii")
    return body + xref + trailer


class FakeHaysProvider(HaysResultsCentreProvider):
    """Offline stand-in that serves fixed HTML/PDF bytes instead of hitting the network."""

    def __init__(
        self,
        pages: dict[str, str] | None = None,
        pdf_pages: dict[str, bytes] | None = None,
        *,
        override_url: str | None = None,
    ) -> None:
        super().__init__(override_url=override_url)
        self.pages = pages or {}
        self.pdf_pages = pdf_pages or {}

    def _fetch_bytes(self, url: str) -> tuple[bytes, str, str]:
        if url in self.pdf_pages:
            return self.pdf_pages[url], "application/pdf", "utf-8"
        return self.pages[url].encode("utf-8"), "text/html; charset=utf-8", "utf-8"


def provider_url() -> str:
    return HaysResultsCentreProvider.RESULTS_URL


class ReleaseIngestionTests(unittest.TestCase):
    def test_no_release_yet_returns_none(self) -> None:
        provider = FakeHaysProvider(
            {provider_url(): '<html><a href="/q4">Quarterly update for the three months ended 30 June 2026</a></html>'}
        )
        self.assertIsNone(provider.discover("hays-fy2026-results"))

    def test_official_full_year_link_is_discovered(self) -> None:
        """Exact current Results Centre title still matches."""
        release_url = "https://www.haysplc.com/results/fy26"
        body = " ".join(["Official FY26 results text"] * 80)
        provider = FakeHaysProvider(
            {
                provider_url(): '<a href="/results/fy26">Full-year results for the year ended 30 June 2026</a>',
                release_url: f"<html><body>{body}</body></html>",
            }
        )
        document = provider.discover("hays-fy2026-results")
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.source_url, release_url)
        self.assertEqual(document.source_type, "company_results")
        self.assertEqual(len(document.content_sha256), 64)

    def test_full_year_without_hyphen_and_bare_year_is_discovered(self) -> None:
        """"full year" (no hyphen) with a bare 2026, no "year ended" phrase."""
        release_url = "https://www.haysplc.com/results/fy26-full-year"
        body = " ".join(["Hays plc full year results text"] * 80)
        provider = FakeHaysProvider(
            {
                provider_url(): '<a href="/results/fy26-full-year">Full year results 2026</a>',
                release_url: f"<html><body>{body}</body></html>",
            }
        )
        document = provider.discover("hays-fy2026-results")
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.source_url, release_url)

    def test_preliminary_results_wording_is_discovered(self) -> None:
        release_url = "https://www.haysplc.com/results/prelims-fy26"
        body = " ".join(["Preliminary results announcement text"] * 80)
        provider = FakeHaysProvider(
            {
                provider_url(): (
                    '<a href="/results/prelims-fy26">'
                    "Preliminary results for the year ended 30 June 2026</a>"
                ),
                release_url: f"<html><body>{body}</body></html>",
            }
        )
        document = provider.discover("hays-fy2026-results")
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.source_url, release_url)

    def test_direct_fy2026_pdf_link_is_downloaded_and_parsed(self) -> None:
        pdf_url = "https://www.haysplc.com/investors/results-centre/documents/hays-fy26-full-year-results.pdf"
        pdf_text = "Hays plc\nFull year results for the year ended 30 June 2026\n" + ("Results body text. " * 60)
        provider = FakeHaysProvider(
            pages={
                provider_url(): f'<a href="{pdf_url}">Results announcement (PDF)</a>',
            },
            pdf_pages={pdf_url: _minimal_pdf_bytes(pdf_text)},
        )
        document = provider.discover("hays-fy2026-results")
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.source_url, pdf_url)
        self.assertEqual(document.source_type, "company_results_pdf")
        self.assertIn("Full year results", document.raw_text)
        self.assertGreaterEqual(len(document.raw_text), 500)

    def test_fy2025_report_is_rejected_even_with_matching_keywords(self) -> None:
        provider = FakeHaysProvider(
            {
                provider_url(): (
                    '<a href="/results/fy25">'
                    "Full-year results for the year ended 30 June 2025</a>"
                ),
            }
        )
        self.assertIsNone(provider.discover("hays-fy2026-results"))

    def test_fy25_token_in_url_is_rejected(self) -> None:
        provider = FakeHaysProvider(
            {
                provider_url(): (
                    '<a href="/investors/results-centre/documents/hays-fy25-full-year-results.pdf">'
                    "Results announcement (PDF)</a>"
                ),
            }
        )
        self.assertIsNone(provider.discover("hays-fy2026-results"))

    def test_irrelevant_link_is_rejected(self) -> None:
        provider = FakeHaysProvider(
            {
                provider_url(): (
                    '<a href="/results/interim-2025">'
                    "Interim results for the six months ended 31 December 2025</a>"
                    '<a href="/news/update">Quarterly trading update</a>'
                ),
            }
        )
        self.assertIsNone(provider.discover("hays-fy2026-results"))

    def test_manual_override_url_is_fetched_directly_without_listing_scan(self) -> None:
        override_url = "https://www.haysplc.com/results/manually-confirmed-release"
        body = " ".join(["Manually confirmed Hays release text"] * 80)

        class ExplodingListingProvider(FakeHaysProvider):
            def _fetch_bytes(self, url: str) -> tuple[bytes, str, str]:
                if url == provider_url():
                    raise AssertionError("listing page must not be fetched when override_url is set")
                return super()._fetch_bytes(url)

        provider = ExplodingListingProvider(
            pages={override_url: f"<html><body>{body}</body></html>"},
            override_url=override_url,
        )
        document = provider.discover("hays-fy2026-results")
        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.source_url, override_url)
        self.assertEqual(document.source_title, "manual-override")

    def test_manual_override_still_rejects_too_short_document(self) -> None:
        override_url = "https://www.haysplc.com/results/too-short"
        provider = FakeHaysProvider(
            pages={override_url: "<html><body>short</body></html>"},
            override_url=override_url,
        )
        self.assertIsNone(provider.discover("hays-fy2026-results"))

    def test_ai_scores_are_bounded_to_strategy_component_budgets(self) -> None:
        analysis = EventAnalysisPayload(
            metrics=[
                {
                    "name": "fy27_operating_profit_pre_exceptional_gbp_m",
                    "value": 61.0,
                    "unit": "GBP million",
                }
            ],
            guidance_summary="FY27 outlook above consensus",
            management_summary="Cost actions progressing",
            catalyst_direction="BULLISH",
            catalyst_score_0_25=24,
            fundamental_direction="BULLISH",
            fundamental_score_0_35=31,
            key_positive_surprises=["FY27 outlook stronger"],
            key_negative_surprises=[],
            uncertainties=["market recovery timing"],
            invalidation_flags=[],
            evidence_quotes=["outlook improved"],
        )
        fundamental, catalyst = event_analysis_components(analysis)
        self.assertEqual(fundamental.direction, Direction.LONG)
        self.assertEqual(fundamental.max_score, 35)
        self.assertEqual(catalyst.max_score, 25)
        self.assertEqual(catalyst.score, 24)


if __name__ == "__main__":
    unittest.main()
