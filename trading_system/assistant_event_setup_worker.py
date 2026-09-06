from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from trading_system.calendar_release_worker import (
    CalendarReleaseTarget,
    SupabaseCalendarReleaseTargetRepository,
)
from trading_system.event_repository import ExpectationPatch
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    SupabaseOfficialReleaseSourceRepository,
)
from trading_system.strategy_draft import StrategyDraftPayload, normalize_draft
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_event_repository import (
    SupabaseTrackedEventRepository,
    TrackedEventTimeStatus,
)


@dataclass(frozen=True)
class AssistantEventSetupRequest:
    request_id: str
    request_key: str
    company_name: str
    instrument: str
    market: str
    kind: str
    title: str
    scheduled_date: date
    event_at: datetime
    event_time_status: TrackedEventTimeStatus
    source_kind: str
    source_url: str
    source_title: str | None
    strategy_payload: dict[str, Any]
    change_note: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AssistantEventSetupRequest":
        event_at = datetime.fromisoformat(str(row["event_at"]).replace("Z", "+00:00"))
        if event_at.tzinfo is None or event_at.utcoffset() is None:
            raise ValueError("assistant setup event_at must be timezone-aware")
        event_at = event_at.astimezone(UTC)
        scheduled_date = date.fromisoformat(str(row["scheduled_date"]))
        if event_at.date() != scheduled_date:
            raise ValueError(
                "assistant setup event_at UTC date must equal scheduled_date; "
                "refusing to invent a cross-date runtime anchor"
            )
        status = TrackedEventTimeStatus(str(row["event_time_status"]))
        if status is TrackedEventTimeStatus.UNKNOWN:
            raise ValueError("assistant setup requires confirmed or estimated event timing")
        strategy_payload = row.get("strategy_payload") or {}
        if not isinstance(strategy_payload, dict):
            raise ValueError("assistant setup strategy_payload must be an object")
        return cls(
            request_id=str(row["id"]),
            request_key=str(row["request_key"]).strip(),
            company_name=str(row["company_name"]).strip(),
            instrument=str(row["instrument"]).strip().upper(),
            market=str(row["market"]).strip(),
            kind=str(row["kind"]).strip(),
            title=str(row["title"]).strip(),
            scheduled_date=scheduled_date,
            event_at=event_at,
            event_time_status=status,
            source_kind=str(row["source_kind"]).strip(),
            source_url=str(row["source_url"]).strip(),
            source_title=(str(row["source_title"]).strip() if row.get("source_title") else None),
            strategy_payload=dict(strategy_payload),
            change_note=str(row["change_note"]).strip(),
        )


class SupabaseAssistantEventSetupQueue:
    TABLE = "assistant_event_setup_requests"

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseAssistantEventSetupQueue":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def claim_one(self) -> AssistantEventSetupRequest | None:
        response = self.client.rpc("claim_assistant_event_setup_request").execute()
        rows = response.data or []
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("assistant setup claim returned invalid data")
        return AssistantEventSetupRequest.from_row(rows[0])

    def complete(self, request: AssistantEventSetupRequest, *, tracked_event_id: str, release_event_id: str) -> None:
        response = (
            self.client.table(self.TABLE)
            .update(
                {
                    "status": "completed",
                    "tracked_event_id": tracked_event_id,
                    "release_event_id": release_event_id,
                    "completed_at": datetime.now(UTC).isoformat(),
                    "last_error": None,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", request.request_id)
            .eq("status", "processing")
            .execute()
        )
        if len(response.data or []) != 1:
            raise RuntimeError("assistant setup completion lost processing ownership")

    def fail(self, request: AssistantEventSetupRequest, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"[:1000]
        response = (
            self.client.table(self.TABLE)
            .update(
                {
                    "status": "failed",
                    "last_error": message,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", request.request_id)
            .eq("status", "processing")
            .execute()
        )
        if len(response.data or []) != 1:
            raise RuntimeError("assistant setup failure write lost processing ownership")


def _strategy_draft(request: AssistantEventSetupRequest, *, release_event_id: str) -> StrategyDraftPayload:
    payload = {
        "instrument": request.instrument,
        "event_name": request.title,
        "scheduled_date": request.scheduled_date.isoformat(),
        "change_note": request.change_note,
        "summary": request.strategy_payload.get("summary") or "Assistant-prepared event strategy",
        "assumptions": request.strategy_payload.get("assumptions") or [],
        "unresolved_questions": request.strategy_payload.get("unresolved_questions") or [],
        "consensus": request.strategy_payload.get("consensus") or {},
        "important_kpis": request.strategy_payload.get("important_kpis") or [],
        "bull_case": request.strategy_payload.get("bull_case") or [],
        "base_case": request.strategy_payload.get("base_case") or [],
        "bear_case": request.strategy_payload.get("bear_case") or [],
        "triggers": request.strategy_payload.get("triggers") or {},
        "invalidation_conditions": request.strategy_payload.get("invalidation_conditions") or [],
        "source_name": request.strategy_payload.get("source_name"),
        "source_url": request.strategy_payload.get("source_url"),
        "source_as_of": request.strategy_payload.get("source_as_of"),
    }
    draft = StrategyDraftPayload.model_validate(payload)
    # normalize now so duplicate whitespace/list entries are resolved before
    # the immutable expectation version is written.
    normalize_draft(release_event_id, draft)
    return draft


def _apply_strategy(
    expectations: SupabaseEventExpectationRepository,
    request: AssistantEventSetupRequest,
    *,
    release_event_id: str,
) -> None:
    draft = _strategy_draft(request, release_event_id=release_event_id)
    current = expectations.get(release_event_id)
    if current is None:
        raise RuntimeError("assistant setup release shell has no current expectation")
    if current.instrument.strip().upper() != request.instrument:
        raise RuntimeError("assistant setup expectation instrument conflict")
    if current.scheduled_date != request.scheduled_date:
        raise RuntimeError("assistant setup expectation date conflict")

    normalized = normalize_draft(release_event_id, draft)
    expectations.apply_partial_update(
        release_event_id,
        ExpectationPatch(
            consensus=dict(normalized["consensus"]),
            important_kpis=tuple(normalized["important_kpis"]),
            bull_case=tuple(normalized["bull_case"]),
            base_case=tuple(normalized["base_case"]),
            bear_case=tuple(normalized["bear_case"]),
            triggers=dict(normalized["triggers"]),
            invalidation_conditions=tuple(normalized["invalidation_conditions"]),
            source_name=normalized["source_name"],
            source_url=normalized["source_url"],
            source_as_of=(
                date.fromisoformat(normalized["source_as_of"])
                if normalized["source_as_of"]
                else None
            ),
        ),
        change_note=request.change_note,
    )


def process_request(
    request: AssistantEventSetupRequest,
    *,
    tracked: SupabaseTrackedEventRepository,
    release_targets: SupabaseCalendarReleaseTargetRepository,
    sources: SupabaseOfficialReleaseSourceRepository,
    expectations: SupabaseEventExpectationRepository,
) -> tuple[str, str]:
    event, _action = tracked.upsert(
        company_name=request.company_name,
        instrument=request.instrument,
        market=request.market,
        source="assistant_setup",
        external_key=f"assistant:{request.request_key}",
        kind=request.kind,
        title=request.title,
        event_at=request.event_at,
        event_time_status=request.event_time_status,
        actor="assistant-event-setup-worker",
    )

    release_event_id = f"tracked:{event.event_id}"
    ensured = release_targets.ensure_release_shell(
        CalendarReleaseTarget(
            calendar_event_id=None,
            event_id=release_event_id,
            ticker=request.instrument,
            scheduled_date=request.scheduled_date,
            market=request.market,
            tracked_event_id=event.event_id,
        )
    )
    if ensured != release_event_id:
        raise RuntimeError("assistant setup release-shell identity mismatch")

    source_state = sources.get_state(release_event_id)
    requested_source = OfficialReleaseSource(
        event_id=release_event_id,
        source_kind=request.source_kind,
        source_url=request.source_url,
        source_title=request.source_title,
    )
    if source_state.source is None:
        sources.set(
            requested_source,
            expected_version=source_state.version,
            actor="assistant-event-setup-worker",
        )
    elif (
        source_state.source.source_kind != requested_source.source_kind
        or source_state.source.source_url != requested_source.source_url
        or source_state.source.source_title != requested_source.source_title
    ):
        raise RuntimeError("assistant setup official release source conflict")

    _apply_strategy(expectations, request, release_event_id=release_event_id)
    return event.event_id, release_event_id


def run_once() -> int:
    queue = SupabaseAssistantEventSetupQueue.from_env()
    request = queue.claim_one()
    if request is None:
        return 0

    tracked = SupabaseTrackedEventRepository.from_env()
    release_targets = SupabaseCalendarReleaseTargetRepository.from_env()
    sources = SupabaseOfficialReleaseSourceRepository.from_env()
    expectations = SupabaseEventExpectationRepository.from_env()

    try:
        tracked_event_id, release_event_id = process_request(
            request,
            tracked=tracked,
            release_targets=release_targets,
            sources=sources,
            expectations=expectations,
        )
        queue.complete(
            request,
            tracked_event_id=tracked_event_id,
            release_event_id=release_event_id,
        )
        print(
            f"assistant setup completed request={request.request_key} "
            f"tracked_event_id={tracked_event_id} release_event_id={release_event_id}"
        )
        return 0
    except Exception as exc:
        queue.fail(request, exc)
        print(f"assistant setup failed request={request.request_key}: {type(exc).__name__}: {exc}")
        return 1


def main() -> int:
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
