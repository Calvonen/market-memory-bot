from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Protocol
from urllib.parse import unquote

from trading_system.results_page_release_candidates import ResultsPageReleaseCandidate


class ResultsPageSelectionTarget(Protocol):
    """The canonical event identity this selection actually reads.

    ``CalendarEvent`` satisfies this protocol, so existing callers keep working
    unchanged. Callers that legitimately hold only part of a calendar row - the
    release worker knows an event's identity and scheduled date but not its
    company name, provider source or occurrence key - pass
    ``ResultsPageSelectionContext`` instead of fabricating canonical fields just
    to fill a dataclass.
    """

    @property
    def calendar_event_id(self) -> str: ...

    @property
    def scheduled_date(self) -> date: ...


@dataclass(frozen=True)
class ResultsPageSelectionContext:
    """Minimal explicit selection identity: who the candidates must belong to.

    ``calendar_event_id`` is compared against ``candidate.event_id``, which the
    extractor stamps from the approved source's ``event_id``. It is an identity
    to match exactly, never a value to re-derive from another string.
    """

    calendar_event_id: str
    scheduled_date: date


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
_ENGLISH_MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_ENGLISH_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _scheduled_date_patterns(event: ResultsPageSelectionTarget) -> tuple[re.Pattern[str], ...]:
    value = event.scheduled_date
    tokens = (
        value.isoformat(),
        value.strftime("%Y%m%d"),
        value.strftime("%Y/%m/%d"),
        value.strftime("%Y_%m_%d"),
    )
    return tuple(
        re.compile(rf"(?<!\d){re.escape(token)}(?!\d)", re.IGNORECASE)
        for token in tokens
    )


def _human_scheduled_date_patterns(
    event: ResultsPageSelectionTarget,
) -> tuple[re.Pattern[str], ...]:
    value = event.scheduled_date
    month_tokens = (
        _ENGLISH_MONTH_ABBREVIATIONS[value.month - 1],
        _ENGLISH_MONTH_NAMES[value.month - 1],
    )
    patterns: list[re.Pattern[str]] = []
    for month in month_tokens:
        for day in (str(value.day), f"{value.day:02d}"):
            token = f"{day} {month} {value.year}"
            # These are deliberately English ASCII month names. ASCII case
            # folding avoids Unicode lookalikes such as dotless-i/long-s being
            # accepted as if they were one of the enumerated spellings.
            patterns.append(re.compile(re.escape(token), re.IGNORECASE | re.ASCII))
    return tuple(patterns)


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


def _is_unicode_noncharacter(codepoint: int) -> bool:
    return (
        0xFDD0 <= codepoint <= 0xFDEF
        or (0 <= codepoint <= 0x10FFFF and codepoint & 0xFFFF in {0xFFFE, 0xFFFF})
    )


def _contains_rejected_decoded_url_char(value: str) -> bool:
    # URL evidence must use the same fail-closed character policy as textual
    # evidence after percent-decoding: neither Unicode controls nor designated
    # noncharacters may manufacture fiscal-period token boundaries.
    return any(
        unicodedata.category(char) == "Cc" or _is_unicode_noncharacter(ord(char))
        for char in value
    )


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
    if _contains_rejected_decoded_url_char(decoded):
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


def _is_explicit_powerpoint_candidate(candidate: ResultsPageReleaseCandidate) -> bool:
    return (candidate.source_title or "").strip().lower() in {"ppt", "pptx", "powerpoint"}


def _matching_candidates(
    event: ResultsPageSelectionTarget,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
    patterns: tuple[re.Pattern[str], ...],
    *,
    standalone_token: bool = False,
) -> tuple[ResultsPageReleaseCandidate, ...]:
    return tuple(
        candidate
        for candidate in candidates
        if not _is_explicit_powerpoint_candidate(candidate)
        and candidate.event_id == event.calendar_event_id
        and any(
            _pattern_has_standalone_match(field, pattern) if standalone_token else bool(pattern.search(field))
            for field in _candidate_evidence_fields(candidate)
            for pattern in patterns
        )
    )


def _scheduled_date_matches(
    event: ResultsPageSelectionTarget,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
) -> tuple[ResultsPageReleaseCandidate, ...]:
    numeric_matches = _matching_candidates(event, candidates, _scheduled_date_patterns(event))
    human_matches = _matching_candidates(
        event,
        candidates,
        _human_scheduled_date_patterns(event),
        standalone_token=True,
    )
    matched_ids = {id(candidate) for candidate in numeric_matches}
    return numeric_matches + tuple(
        candidate for candidate in human_matches if id(candidate) not in matched_ids
    )


def select_results_page_release_candidate(
    event: ResultsPageSelectionTarget,
    candidates: tuple[ResultsPageReleaseCandidate, ...],
    *,
    release_period: str | None = None,
) -> ResultsPageSelection:
    """Select a unique release candidate using explicit evidence only."""
    period_patterns = _period_patterns(release_period)

    date_matches = _scheduled_date_matches(event, candidates)
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