from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


def _source() -> OfficialReleaseSource:
    return OfficialReleaseSource(
        event_id="evt",
        source_url="https://example.com/results",
        source_kind="results_page",
    )


def test_template_local_anchor_end_does_not_clear_outer_formatting_anchor() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/q">Report<template><a href="/other">hidden</a></template> Q2-2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/q"
    assert candidates[0].evidence_fields == ("Report Q2-2026",)


def test_stack_only_block_pop_preserves_rendered_boundary() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<p><a href="/q">Q2</p>-2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/q"
    assert candidates[0].evidence_fields == ("Q2 -2026",)


def test_implied_close_stops_at_foreign_scope_marker() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<p hidden>old<svg><foreignObject><li><a href="/r">Q2-2026</a></li></foreignObject></svg></p>',
    )
    assert candidates == ()


def test_remaining_paragraph_block_start_preserves_rendered_break() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/r">Q2<details>-2026</details></a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/r"
    assert candidates[0].evidence_fields == ("Q2 -2026",)
