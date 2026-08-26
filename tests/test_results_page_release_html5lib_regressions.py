from trading_system.official_release_source_repository import OfficialReleaseSource
from trading_system.results_page_release_candidates import extract_results_page_candidates


def _source() -> OfficialReleaseSource:
    return OfficialReleaseSource(
        event_id="evt",
        source_url="https://example.com/results",
        source_kind="results_page",
    )


def test_template_local_unmatched_anchor_close_is_noop_for_outer_anchor() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/q">Report<template></a></template> Q2-2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/q"
    assert candidates[0].evidence_fields == ("Report Q2-2026",)


def test_hidden_anchor_attributes_survive_html5_reconstruction() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<p hidden><a href="/q">old</p>Q2-2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/q"
    assert candidates[0].evidence_fields == ("Q2-2026",)


def test_reconstructed_anchor_fragments_keep_evidence_fields_separate() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<div><a href="/q">Q2</div>2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/q"
    assert candidates[0].evidence_fields == ("Q2", "2026")


def test_hidden_rendered_break_does_not_create_period_boundary() -> None:
    candidates = extract_results_page_candidates(
        _source(),
        '<a href="/r">A<br hidden>Q2-2026</a>',
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.com/r"
    assert candidates[0].evidence_fields == ("AQ2-2026",)
