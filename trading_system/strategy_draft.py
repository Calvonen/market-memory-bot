from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator

from trading_system.models import EventExpectation


class StrategyDraftPayload(BaseModel):
    """A human/AI-authored earnings-event strategy draft.

    Mirrors the fields of the immutable, versioned EventExpectation plus
    fields meant for a human reviewer (summary/assumptions/unresolved
    questions) that are never persisted onto EventExpectation itself. This
    model is the request body for both the preview and approve endpoints -
    approve must receive exactly what preview validated, which is why the
    same shape (and the same normalization) is used for both.
    """

    instrument: str = Field(min_length=1, max_length=32)
    event_name: str = Field(min_length=1, max_length=200)
    scheduled_date: date
    consensus: dict[str, float | str | None] = Field(default_factory=dict)
    important_kpis: list[str] = Field(default_factory=list)
    bull_case: list[str] = Field(default_factory=list)
    base_case: list[str] = Field(default_factory=list)
    bear_case: list[str] = Field(default_factory=list)
    triggers: dict[str, float | str] = Field(default_factory=dict)
    invalidation_conditions: list[str] = Field(default_factory=list)
    source_name: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=2000)
    source_as_of: date | None = None
    change_note: str = Field(min_length=3, max_length=500)
    summary: str = Field(min_length=1, max_length=2000)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)

    @field_validator("change_note", "summary", mode="before")
    @classmethod
    def _strip_before_length_check(cls, value: Any) -> Any:
        # Pydantic's min_length constraint runs on whatever a "before"
        # validator returns, so stripping here - not in normalize_draft(),
        # which only runs after validation already passed - is what makes
        # a whitespace-only value ("   ") actually fail min_length instead
        # of sailing through as "long enough" and only becoming empty later.
        if isinstance(value, str):
            return value.strip()
        return value


def _clean_list(items: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in items:
        value = raw.strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def normalize_draft(event_id: str, payload: StrategyDraftPayload) -> dict[str, Any]:
    """Produce the canonical, JSON-safe draft shape used for the fingerprint,
    the preview response, and the persisted expectation version alike.

    Normalizing once and reusing the result everywhere is what makes the
    preview -> approve fingerprint check meaningful: approve recomputes this
    same function over the submitted draft and compares digests, so any
    change to the payload after preview - even just whitespace - is caught.
    """
    return {
        "event_id": event_id,
        "instrument": payload.instrument.strip(),
        "event_name": payload.event_name.strip(),
        "scheduled_date": payload.scheduled_date.isoformat(),
        "consensus": dict(payload.consensus),
        "important_kpis": _clean_list(payload.important_kpis),
        "bull_case": _clean_list(payload.bull_case),
        "base_case": _clean_list(payload.base_case),
        "bear_case": _clean_list(payload.bear_case),
        "triggers": dict(payload.triggers),
        "invalidation_conditions": _clean_list(payload.invalidation_conditions),
        "source_name": _clean_str(payload.source_name),
        "source_url": _clean_str(payload.source_url),
        "source_as_of": payload.source_as_of.isoformat() if payload.source_as_of else None,
        "change_note": payload.change_note.strip(),
        "summary": payload.summary.strip(),
        "assumptions": _clean_list(payload.assumptions),
        "unresolved_questions": _clean_list(payload.unresolved_questions),
    }


def draft_fingerprint(normalized: dict[str, Any]) -> str:
    """A stable content hash of a normalized draft.

    Used as an optimistic-concurrency token: approve recomputes it from the
    submitted draft and rejects the request (409) if it does not match the
    fingerprint the caller says it previewed against, so an approved payload
    can never silently differ from what a human (or assistant) reviewed.
    """
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_METRIC_NORMALIZATION_SUFFIX = "_pre_exceptional"


def _normalized_metric_key(value: str) -> str:
    return value.replace(_METRIC_NORMALIZATION_SUFFIX, "")


def draft_warnings(normalized: dict[str, Any], current: EventExpectation | None) -> list[str]:
    """Soft, non-blocking conflict/completeness warnings for a preview.

    These never raise - required-field validation is handled by
    StrategyDraftPayload itself (422 on missing/invalid fields). Warnings
    surface things a reviewer should notice before approving: KPIs or
    triggers that don't line up with the consensus dict, empty scenarios,
    missing NO TRADE conditions, a missing source, or drift versus the
    event identity already on file.
    """
    warnings: list[str] = []
    consensus_keys = set(normalized["consensus"])
    normalized_consensus_keys = {_normalized_metric_key(key) for key in consensus_keys}

    for kpi in normalized["important_kpis"]:
        if kpi not in consensus_keys:
            warnings.append(
                f"important_kpis sisältää avaimen '{kpi}', jota ei löydy consensuksesta"
            )

    for trigger_name in normalized["triggers"]:
        if trigger_name.startswith("bull_") or trigger_name.startswith("bear_"):
            metric_key = trigger_name.split("_", 1)[1]
        else:
            warnings.append(
                f"trigger '{trigger_name}' ei ala etuliitteellä 'bull_' tai 'bear_'"
            )
            continue
        if _normalized_metric_key(metric_key) not in normalized_consensus_keys:
            warnings.append(
                f"trigger '{trigger_name}' ei vastaa mitään consensus-avainta"
            )

    if not normalized["bull_case"]:
        warnings.append("bull-skenaario on tyhjä")
    if not normalized["base_case"]:
        warnings.append("base-skenaario on tyhjä")
    if not normalized["bear_case"]:
        warnings.append("bear-skenaario on tyhjä")
    if not normalized["invalidation_conditions"]:
        warnings.append("mitätöintiehtoja (NO TRADE -ehtoja) ei ole määritelty")
    if not normalized["source_url"] and not normalized["source_name"]:
        warnings.append("lähdettä ei ole merkitty")
    if normalized["unresolved_questions"]:
        count = len(normalized["unresolved_questions"])
        warnings.append(f"{count} ratkaisematonta kysymystä ennen hyväksyntää")

    if current is not None:
        for mismatch in identity_mismatches(normalized, current):
            warnings.append(f"{mismatch} - tarkista, ettei luonnos ole väärän eventin")

    return warnings


def identity_mismatches(normalized: dict[str, Any], current: EventExpectation) -> list[str]:
    """Which identity fields (instrument/event_name/scheduled_date) differ
    between the draft and the event identified by the URL's {event_id}.

    event_id itself is never client-controllable - normalize_draft() always
    takes it from the URL path, never from the request body - so it cannot
    drift by construction. These three fields *are* present in the request
    body, so a draft built against the wrong event (or hand-edited
    carelessly) could otherwise silently retarget/rename/reschedule a
    different event. preview() surfaces a mismatch here only as a warning;
    approve() must treat a non-empty result as a hard failure - the
    {event_id} in the URL is authoritative and a draft must never be able
    to override it.
    """
    mismatches: list[str] = []
    if normalized["instrument"] != current.instrument:
        mismatches.append(
            f"instrument '{normalized['instrument']}' poikkeaa nykyisestä ('{current.instrument}')"
        )
    if normalized["event_name"] != current.event_name:
        mismatches.append(
            f"event_name '{normalized['event_name']}' poikkeaa nykyisestä ('{current.event_name}')"
        )
    if normalized["scheduled_date"] != current.scheduled_date.isoformat():
        mismatches.append(
            f"scheduled_date '{normalized['scheduled_date']}' poikkeaa nykyisestä "
            f"('{current.scheduled_date.isoformat()}')"
        )
    return mismatches


_DIFF_FIELDS = (
    "instrument",
    "event_name",
    "scheduled_date",
    "consensus",
    "important_kpis",
    "bull_case",
    "base_case",
    "bear_case",
    "triggers",
    "invalidation_conditions",
    "source_name",
    "source_url",
    "source_as_of",
)


def changed_fields(normalized: dict[str, Any], current: EventExpectation | None) -> list[str]:
    """Which EventExpectation-shaped fields the draft would change, versus
    the currently persisted (approved) version - used by preview to show
    "what's different" without requiring the caller to diff it themselves."""
    if current is None:
        return list(_DIFF_FIELDS)

    current_payload = {
        "instrument": current.instrument,
        "event_name": current.event_name,
        "scheduled_date": current.scheduled_date.isoformat(),
        "consensus": current.consensus,
        "important_kpis": list(current.important_kpis),
        "bull_case": list(current.bull_case),
        "base_case": list(current.base_case),
        "bear_case": list(current.bear_case),
        "triggers": current.triggers,
        "invalidation_conditions": list(current.invalidation_conditions),
        "source_name": current.source_name,
        "source_url": current.source_url,
        "source_as_of": current.source_as_of.isoformat() if current.source_as_of else None,
    }
    return [field for field in _DIFF_FIELDS if normalized[field] != current_payload[field]]
