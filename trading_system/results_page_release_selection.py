from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from trading_system.calendar_repository import CalendarEvent
from trading_system.results_page_release_candidates import ResultsPageReleaseCandidate


class ResultsPageSelectionStatus(str, Enum):
    SELECTED = "selected"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ResultsPageSelection:
    status: ResultsPageSelectionStatus
    candidate: ResultsPageReleaseCandidate | None = None


_PERIOD_LABEL_RE = re.compile(r"^(Q[1-4]|H[12]|FY) ([0-9]{4})$", re.IGNORECASE)
_UNICODE_ALNUM = r"[^\W_]"


def _scheduled_date_patterns(event: CalendarEvent) -> tuple[re.Pattern[str], ...]:
    value = event.scheduled_date
    tokens = (
        value.isoformat(),
        value.strftime("%Y%m%d"),
        value.strftime("%Y/%m/%d"),
        value.strftime("%Y_%m_%d"),
    )
    return tuple(re.compile(rf"(?<!\d){re.escape(token)}(?!\d)", re.IGNORECASE) for token in tokens)


def _period_patterns(release_period: str | None) -> tuple[re.Pattern[str], ...]:
    if release_period is None:
        return ()
    match = _PERIOD_LABEL_RE.fullmatch(release_period.strip())
    if match is None:
        raise ValueError("release_period must be Q1-Q4, H1-H2, or FY followed by one ASCII space and a four-digit ASCII year")
    period, year = match.groups()
    separator = r"(?:[ _\-/]+)"
    return (
        re.compile(rf"(?<!{_UNICODE_ALNUM}){period}{separator}{year}(?!{_UNICODE_ALNUM})", re.IGNORECASE),
        re.compile(rf"(?<!{_UNICODE_ALNUM}){period}{year}(?!{_UNICODE_ALNUM})", re.IGNORECASE),
    )


def _candidate_evidence_fields(candidate: ResultsPageReleaseCandidate) -> tuple[str, ...]:
    fields = [candidate.source_url.casefold()]
    if candidate.source_title:
        fields.append(candidate.source_title.casefold())
    return tuple(fields)


def _matching_candidates(
    event: CalendarEvent,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
    patterns: tuple[re.Pattern[str], ...],
) -> tuple[ResultsPageReleaseCandidate, ...]:
    return tuple(
        candidate
        for candidate in candidates
        if candidate.event_id == event.calendar_event_id
        and any(
            pattern.search(field)
            for field in _candidate_evidence_fields(candidate)
            for pattern in patterns
        )
    )


def select_results_page_release_candidate(
    event: CalendarEvent,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
    *,
    release_period: str | None = None,
) -> ResultsPageSelection:
    """Select a unique release candidate using explicit evidence only."""
    period_patterns = _period_patterns(release_period)

    date_matches = _matching_candidates(event, candidates, _scheduled_date_patterns(event))
    if len(date_matches) == 1:
        return ResultsPageSelection(
            status=ResultsPageSelectionStatus.SELECTED,
            candidate=date_matches[0],
        )
    if len(date_matches) > 1:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.AMBIGUOUS)

    if not period_patterns:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.NO_MATCH)

    period_matches = _matching_candidates(event, candidates, period_patterns)
    if not period_matches:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.NO_MATCH)
    if len(period_matches) != 1:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.AMBIGUOUS)
    return ResultsPageSelection(
        status=ResultsPageSelectionStatus.SELECTED,
        candidate=period_matches[0],
    )
