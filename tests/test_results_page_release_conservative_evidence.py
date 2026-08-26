from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


def _source() -> OfficialReleaseSource:
    return OfficialReleaseSource(
        event_id="evt",
        source_url="https://example.com/results",
        source_kind="results_page",
    )


def test_literal_surrogate_text_is_rejected_fail_closed() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/r">Annual\ud800Q2-2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/r"
    assert candidates[0].evidence_fields == ()


def test_datalist_subtree_is_an_evidence_boundary() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<datalist><a href="/r">Q2-2026</a></datalist>',
    )
    assert candidates == ()


def test_svg_definition_subtree_is_an_evidence_boundary() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/r">Annual<svg><defs><text>Q2-2026</text></defs></svg></a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/r"
    assert candidates[0].evidence_fields == ("Annual",)


def test_svg_switch_subtree_is_an_evidence_boundary() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/r">Annual<svg><switch><text>Annual</text><text>Q2-2026</text></switch></svg></a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/r"
    assert candidates[0].evidence_fields == ("Annual",)


def test_foreign_icon_does_not_hide_safe_html_tail_text() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/r"><svg><path d="M0 0"/></svg> Q2-2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/r"
    assert candidates[0].evidence_fields == ("Q2-2026",)


def test_ambiguous_subtree_splits_evidence_fields() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/r">Q2<svg><switch><text>unknown</text></switch></svg>-2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/r"
    assert candidates[0].evidence_fields == ("Q2", "-2026")


def test_table_caption_remains_simple_rendered_html() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<table><caption><a href="/r">Q2-2026</a></caption></table>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/r"
    assert candidates[0].evidence_fields == ("Q2-2026",)
