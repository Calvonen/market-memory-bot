from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote

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
_PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_ZERO_WIDTH_SPACE = "\u200b"


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
    match = _PERIOD_LABEL_RE.fullmatch(release_period)
    if match is None:
        raise ValueError("release_period must be Q1-Q4, H1-H2, or FY followed by one ASCII space and a four-digit ASCII year")
    period, year = match.groups()
    separator = r"(?:[ _\-/]+)"
    return (
        re.compile(rf"{period}{separator}{year}", re.IGNORECASE),
        re.compile(rf"{period}{year}", re.IGNORECASE),
    )


def _is_token_char(char: str) -> bool:
    category = unicodedata.category(char)
    # U+200B is a deliberate word separator even though it is category Cf.
    # Other format controls, including ZWNJ/ZWJ, must not manufacture token
    # boundaries around fiscal-period evidence.
    return (
        char.isalnum()
        or category.startswith("M")
        or (category == "Cf" and char != _ZERO_WIDTH_SPACE)
    )


def _pattern_has_standalone_match(field: str, pattern: re.Pattern[str]) -> bool:
    for match in pattern.finditer(field):
        before = field[match.start() - 1] if match.start() > 0 else ""
        after = field[match.end()] if match.end() < len(field) else ""
        if (not before or not _is_token_char(before)) and (not after or not _is_token_char(after)):
            return True
    return False


def _contains_unicode_control(value: str) -> bool:
    # Unicode category Cc covers both ASCII controls and the C1 control range
    # such as U+0085. Those characters must never manufacture evidence
    # boundaries after URL percent-decoding.
    return any(unicodedata.category(char) == "Cc" for char in value)


def _decoded_url_evidence(source_url: str) -> str | None:
    """Percent-decode URL evidence without letting malformed escapes broaden matches."""
    index = 0
    while index < len(source_url):
        if source_url[index] != "%":
            index += 1
            continue
        if _PERCENT_ESCAPE_RE.match(source_url, index) is None:
            return None
        index += 3
    try:
        decoded = unquote(source_url, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if _contains_unicode_control(decoded):
        return None
    return decoded


def _candidate_evidence_fields(candidate: ResultsPageReleaseCandidate) -> tuple[str, ...]:
    fields: list[str] = []
    decoded_url = _decoded_url_evidence(candidate.source_url)
    if decoded_url is not None:
        fields.append(decoded_url)
    if candidate.evidence_fields:
        fields.extend(candidate.evidence_fields)
    elif candidate.source_title:
        fields.append(candidate.source_title)
    return tuple(fields)


def _matching_candidates(
    event: CalendarEvent,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
    patterns: tuple[re.Pattern[str], ...],
    *,
    standalone_token: bool = False,
) -> tuple[ResultsPageReleaseCandidate, ...]:
    return tuple(
        candidate
        for candidate in candidates
        if candidate.event_id == event.calendar_event_id
        and any(
            _pattern_has_standalone_match(field, pattern) if standalone_token else bool(pattern.search(field))
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

    period_matches = _matching_candidates(
        event,
        candidates,
        period_patterns,
        standalone_token=True,
    )
    if not period_matches:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.NO_MATCH)
    if len(period_matches) != 1:
        return ResultsPageSelection(status=ResultsPageSelectionStatus.AMBIGUOUS)
    return ResultsPageSelection(
        status=ResultsPageSelectionStatus.SELECTED,
        candidate=period_matches[0],
    )
