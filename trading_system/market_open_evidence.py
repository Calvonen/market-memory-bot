from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_system.market_open_paper import MarketOpenPattern
from trading_system.models import ComponentAssessment, Direction, EventExpectation
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
)


_PROVIDER = "rule_engine"
_MODEL = "market-open-v1"


@dataclass(frozen=True)
class FrozenMarketOpenEvidence:
    analysis_id: str
    source_document_id: str
    pattern: MarketOpenPattern
    raw_text: str
    created: bool


def _direction_label(direction: Direction) -> str:
    if direction is Direction.LONG:
        return "BULLISH"
    if direction is Direction.SHORT:
        return "BEARISH"
    raise ValueError("market-open evidence direction must be LONG or SHORT")


def _canonical_raw_text(
    *,
    event: PersistentTrackedEvent,
    expectation: EventExpectation,
    pattern: MarketOpenPattern,
    reactions: tuple[TrackedEventReactionRecord, ...],
) -> str:
    if event.reference_price is None:
        raise ValueError("market-open evidence requires reference price")
    opening_rows = []
    for row in reactions:
        if row.tracked_market_event_id != event.event_id or row.interval_minutes != 1:
            continue
        opening_rows.append(
            {
                "candle_start": row.candle_start.astimezone(UTC).isoformat(),
                "observed_at": row.observed_at.astimezone(UTC).isoformat(),
                "reference_price": str(row.reference_price),
                "close_price": str(row.close_price),
                "return_pct": str(row.return_pct),
                "direction": row.direction,
            }
        )
    payload = {
        "schema": "market-open-evidence-v1",
        "tracked_event_id": event.event_id,
        "source_event_id": expectation.event_id,
        "expectation_version": expectation.version,
        "instrument": event.instrument,
        "event_at": event.event_at.astimezone(UTC).isoformat(),
        "reference_price": str(event.reference_price),
        "reference_captured_at": (
            event.reference_captured_at.astimezone(UTC).isoformat()
            if event.reference_captured_at is not None
            else None
        ),
        "reference_kind": event.reference_kind,
        "pattern": {
            "direction": pattern.direction.value,
            "setup_score": pattern.setup.score,
            "setup_max_score": pattern.setup.max_score,
            "confirmation_score": pattern.confirmation.score,
            "confirmation_max_score": pattern.confirmation.max_score,
            "reaction_pct": str(pattern.reaction_pct),
            "setup_reasons": list(pattern.setup.reasons),
            "confirmation_reasons": list(pattern.confirmation.reasons),
        },
        "opening_reactions": sorted(opening_rows, key=lambda row: row["candle_start"]),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _analysis_payload(pattern: MarketOpenPattern) -> dict[str, Any]:
    label = _direction_label(pattern.direction)
    return {
        "metrics": [],
        "guidance_summary": "Deterministic market-open rule-engine evidence; no company guidance analyzed.",
        "management_summary": "No management statement is used by the market-open strategy.",
        "catalyst_direction": label,
        "catalyst_score_0_25": pattern.confirmation.score,
        "fundamental_direction": label,
        "fundamental_score_0_35": pattern.setup.score,
        "key_positive_surprises": [],
        "key_negative_surprises": [],
        "uncertainties": [
            "Technical and Market Memory must still confirm the same direction before execution."
        ],
        "invalidation_flags": [],
        "evidence_quotes": [],
    }


def _pattern_from_raw_text(
    raw_text: str,
    *,
    event: PersistentTrackedEvent,
    expectation: EventExpectation,
) -> MarketOpenPattern:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("frozen market-open evidence is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "market-open-evidence-v1":
        raise RuntimeError("frozen market-open evidence schema is invalid")
    if str(payload.get("tracked_event_id") or "") != event.event_id:
        raise RuntimeError("frozen market-open evidence belongs to a different tracked event")
    if str(payload.get("source_event_id") or "") != expectation.event_id:
        raise RuntimeError("frozen market-open evidence belongs to a different source event")
    try:
        version = int(payload.get("expectation_version"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("frozen market-open evidence expectation version is invalid") from exc
    if version != expectation.version:
        raise RuntimeError("frozen market-open evidence expectation version changed")
    if str(payload.get("instrument") or "").strip().upper() != event.instrument.strip().upper():
        raise RuntimeError("frozen market-open evidence instrument changed")
    if str(payload.get("event_at") or "") != event.event_at.astimezone(UTC).isoformat():
        raise RuntimeError("frozen market-open evidence event time changed")
    if event.reference_price is None or str(payload.get("reference_price") or "") != str(event.reference_price):
        raise RuntimeError("frozen market-open evidence reference price changed")
    if str(payload.get("reference_kind") or "") != str(event.reference_kind or ""):
        raise RuntimeError("frozen market-open evidence reference kind changed")

    pattern_payload = payload.get("pattern")
    if not isinstance(pattern_payload, dict):
        raise RuntimeError("frozen market-open evidence pattern is missing")
    try:
        direction = Direction(str(pattern_payload["direction"]))
        setup_score = int(pattern_payload["setup_score"])
        setup_max = int(pattern_payload["setup_max_score"])
        confirmation_score = int(pattern_payload["confirmation_score"])
        confirmation_max = int(pattern_payload["confirmation_max_score"])
        reaction_pct = Decimal(str(pattern_payload["reaction_pct"]))
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise RuntimeError("frozen market-open evidence pattern is malformed") from exc
    if direction not in (Direction.LONG, Direction.SHORT):
        raise RuntimeError("frozen market-open evidence direction is invalid")
    if (setup_score, setup_max, confirmation_score, confirmation_max) != (35, 35, 25, 25):
        raise RuntimeError("frozen market-open evidence scores differ from reviewed strategy")
    if not reaction_pct.is_finite():
        raise RuntimeError("frozen market-open evidence reaction is invalid")

    setup_reasons = tuple(str(item) for item in pattern_payload.get("setup_reasons") or ())
    confirmation_reasons = tuple(
        str(item) for item in pattern_payload.get("confirmation_reasons") or ()
    )
    if not setup_reasons or not confirmation_reasons:
        raise RuntimeError("frozen market-open evidence reasons are missing")
    return MarketOpenPattern(
        direction=direction,
        setup=ComponentAssessment("fundamental", direction, 35, 35, setup_reasons),
        confirmation=ComponentAssessment("catalyst", direction, 25, 25, confirmation_reasons),
        reaction_pct=reaction_pct,
    )


def freeze_or_load_market_open_evidence(
    client: Any,
    *,
    event: PersistentTrackedEvent,
    expectation: EventExpectation,
    pattern: MarketOpenPattern,
    reactions: tuple[TrackedEventReactionRecord, ...],
) -> FrozenMarketOpenEvidence:
    raw_text = _canonical_raw_text(
        event=event,
        expectation=expectation,
        pattern=pattern,
        reactions=reactions,
    )
    response = client.rpc(
        "freeze_market_open_evidence",
        {
            "input_tracked_event_id": event.event_id,
            "input_expectation_version": expectation.version,
            "input_raw_text": raw_text,
            "input_analysis": _analysis_payload(pattern),
        },
    ).execute()
    rows = response.data or []
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError("freeze_market_open_evidence returned invalid data")
    row = rows[0]
    persisted_raw_text = str(row.get("out_raw_text") or "")
    persisted_analysis = row.get("out_analysis")
    if not isinstance(persisted_analysis, dict):
        raise RuntimeError("frozen market-open analysis payload is missing")
    expected_label = _direction_label(
        _pattern_from_raw_text(
            persisted_raw_text,
            event=event,
            expectation=expectation,
        ).direction
    )
    if (
        persisted_analysis.get("fundamental_direction") != expected_label
        or persisted_analysis.get("catalyst_direction") != expected_label
        or persisted_analysis.get("fundamental_score_0_35") != 35
        or persisted_analysis.get("catalyst_score_0_25") != 25
    ):
        raise RuntimeError("frozen market-open analysis disagrees with reaction evidence")
    return FrozenMarketOpenEvidence(
        analysis_id=str(row["out_analysis_id"]),
        source_document_id=str(row["out_source_document_id"]),
        pattern=_pattern_from_raw_text(
            persisted_raw_text,
            event=event,
            expectation=expectation,
        ),
        raw_text=persisted_raw_text,
        created=bool(row.get("out_created")),
    )
