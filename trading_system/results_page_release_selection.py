from __future__ import annotations

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


def _scheduled_date_tokens(event: CalendarEvent) -> tuple[str, ...]:
    value = event.scheduled_date
    return (
        value.isoformat(),
        value.strftime("%Y%m%d"),
        value.strftime("%Y/%m/%d"),
        value.strftime("%Y_%m_%d"),
    )


def _candidate_evidence_text(candidate: ResultsPageReleaseCandidate) -> str:
    return f"{candidate.source_url}\n{candidate.source_title or ''}".casefold()


def select_results_page_release_candidate(
    event: CalendarEvent,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
) -> ResultsPageSelection:
    """Select only a uniquely date-anchored release candidate.

    This intentionally uses a narrow high-confidence rule. A candidate must
    contain the canonical event's scheduled date in its URL or title. If zero
    or multiple candidates match, selection fails closed instead of guessing.
    """
    tokens = tuple(token.casefold() for token in _scheduled_date_tokens(event))
    matches = tuple(
        candidate
        for candidate in candidates
        if candidate.event_id == event.calendar_event_id
        and any(token in _candidate_evidence_text(candidate) for token in tokens)
    )

    if not matches:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.NO_MATCH)
    if len(matches) != 1:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.AMBIGUOUS)
    return ResultsPageSelection(
        status=ResultsPageSelectionStatus.SELECTED,
        candidate=matches[0],
    )
