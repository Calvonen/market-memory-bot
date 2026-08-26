from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates
from trading_system.results_page_release_selection import _decoded_url_evidence


def _source() -> OfficialReleaseSource:
    return OfficialReleaseSource(
        event_id="evt",
        source_url="https://example.com/results",
        source_kind="results_page",
    )


def test_percent_decoded_unicode_noncharacter_is_rejected_fail_closed() -> None:
    assert _decoded_url_evidence("https://example.com/A%EF%BF%BFQ2-2026.pdf") is None


def test_rendered_fieldset_legend_release_anchor_is_discoverable() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<fieldset><legend><a href="/r">Q2-2026</a></legend></fieldset>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/r"
    assert candidates[0].evidence_fields == ("Q2-2026",)
