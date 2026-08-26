from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


def _source() -> OfficialReleaseSource:
    return OfficialReleaseSource(
        event_id="evt",
        source_url="https://example.com/results",
        source_kind="results_page",
    )


def test_list_item_start_closes_hidden_paragraph_before_visible_anchor() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<p hidden>old<li><a href="/r">Q2-2026</a></li>',
    )
    assert [candidate.source_url for candidate in candidates] == ["https://example.com/r"]


def test_legacy_block_start_preserves_rendered_break_inside_anchor() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/r">Q2<center>-2026</center></a>',
    )
    assert len(candidates) == 1
    assert candidates[0].evidence_fields == ("Q2 -2026",)


def test_formatting_anchor_survives_stack_only_paragraph_pop() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<p><a href="/q">Report</p> Q2-2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/q"
    assert candidates[0].evidence_fields == ("Report Q2-2026",)
