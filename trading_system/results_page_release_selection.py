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


def _scheduled_date_patterns(event: CalendarEvent) -> tuple[re.Pattern[str], ...]:
    value = event.scheduled_date
    tokens = (
        value.isoformat(),
        value.strftime("%Y%m%d"),
        value.strftime("%Y/%m/%d"),
        value.strftime("%Y_%m_%d"),
    )
    # Date evidence must be a complete numeric token. This prevents compact
    # dates embedded in longer identifiers (20260826001) and separated forms
    # with an extra digit (2026-08-260) from being treated as exact evidence.
    return tuple(re.compile(rf"(?<!\d){re.escape(token)}(?!\d)", re.IGNORECASE) for token in tokens)


def _candidate_evidence_text(candidate: ResultsPageReleaseCandidate) -> str:
    return f"{candidate.source_url}\n{candidate.source_title or ''}".casefold()


def select_results_page_release_candidate(
    event: CalendarEvent,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
) -> ResultsPageSelection:
    """Select only a uniquely date-anchored release candidate.

    This intentionally uses a narrow high-confidence rule. A candidate must
    contain the canonical event's scheduled date in its URL or title as a
    complete date token. If zero or multiple candidates match, selection fails
    closed instead of guessing.
    """
    patterns = _scheduled_date_patterns(event)
    matches = tuple(
        candidate
        for candidate in candidates
        if candidate.event_id == event.calendar_event_id
        and any(pattern.search(_candidate_evidence_text(candidate)) for pattern in patterns)
    )

    if not matches:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.NO_MATCH)
    if len(matches) != 1:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.AMBIGUOUS)
    return ResultsPageSelection(
        status=ResultsPageSelectionStatus.SELECTED,
        candidate=matches[0],
    )
