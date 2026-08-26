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


_PERIOD_LABEL_RE = re.compile(r"^(Q[1-4]|H[12]|FY)\s+(\d{4})$", re.IGNORECASE)


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


def _period_patterns(release_period: str | None) -> tuple[re.Pattern[str], ...]:
    if release_period is None:
        return ()
    match = _PERIOD_LABEL_RE.fullmatch(release_period.strip())
    if match is None:
        raise ValueError("release_period must be Q1-Q4, H1-H2, or FY followed by a four-digit year")
    period, year = match.groups()
    # The caller supplies trusted fiscal-period evidence. Support common URL
    # and title spellings without inferring any period from the release date.
    separator = r"(?:[\s_\-/]+)"
    return (
        re.compile(rf"(?<![A-Za-z0-9]){period}{separator}{year}(?!\d)", re.IGNORECASE),
        re.compile(rf"(?<![A-Za-z0-9]){period}{year}(?!\d)", re.IGNORECASE),
    )


def _candidate_evidence_text(candidate: ResultsPageReleaseCandidate) -> str:
    return f"{candidate.source_url}\n{candidate.source_title or ''}".casefold()


def _matching_candidates(
    event: CalendarEvent,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
    patterns: tuple[re.Pattern[str], ...],
) -> tuple[ResultsPageReleaseCandidate, ...]:
    return tuple(
        candidate
        for candidate in candidates
        if candidate.event_id == event.calendar_event_id
        and any(pattern.search(_candidate_evidence_text(candidate)) for pattern in patterns)
    )


def select_results_page_release_candidate(
    event: CalendarEvent,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
    *,
    release_period: str | None = None,
) -> ResultsPageSelection:
    """Select a unique release candidate using explicit evidence only.

    Exact scheduled-date evidence is the strongest tier and is evaluated
    first. Optional fiscal-period evidence (for example ``Q2 2026`` or
    ``H1 2026``) is used only when no exact-date candidate matches, and only
    when the caller supplies that period explicitly. The period is never
    inferred from the scheduled date. Any ambiguity fails closed.
    """
    date_matches = _matching_candidates(event, candidates, _scheduled_date_patterns(event))
    if len(date_matches) == 1:
        return ResultsPageSelection(
            status=ResultsPageSelectionStatus.SELECTED,
            candidate=date_matches[0],
        )
    if len(date_matches) > 1:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.AMBIGUOUS)

    period_patterns = _period_patterns(release_period)
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
