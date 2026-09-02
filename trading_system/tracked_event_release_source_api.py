from __future__ import annotations

import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from trading_system.models import TradingMode
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceEventNotFound,
    OfficialReleaseSourceState,
    OfficialReleaseSourceVersionConflict,
)
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_event_release_source import (
    build_tracked_event_release_source_read_model,
)
from trading_system.tracked_event_repository import PersistentTrackedEvent
from trading_system.trading_task import TradingTaskState
from trading_system.trading_task_repository import SupabaseTradingTaskRepository
from trading_system.workflow_readiness_evidence_loader import canonical_release_event_id


_POSTGRES_UUID_TEXT = re.compile(
    r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
_TRACKED_EVENT_ACTIVE_HISTORY_WINDOW = timedelta(hours=24)
_ALWAYS_ACTIVE_TRACKED_EVENT_STATUSES = {"tracked", "monitoring"}
_TERMINAL_TRACKED_EVENT_STATUSES = {"completed", "cancelled", "failed"}
_ACTIVITY_BATCH_MAX_IDS = 40


class TrackedEventRepository(Protocol):
    def get(self, event_id: str) -> PersistentTrackedEvent | None: ...

    def get_by_occurrences(
        self,
        *,
        event_ids: tuple[str, ...] = (),
        calendar_event_ids: tuple[str, ...] = (),
    ) -> tuple[PersistentTrackedEvent, ...]: ...


class OfficialReleaseSourceRepository(Protocol):
    def get_state(self, event_id: str) -> OfficialReleaseSourceState: ...

    def set(
        self, source: OfficialReleaseSource, *, expected_version: int, actor: str
    ) -> OfficialReleaseSource: ...


class OfficialReleaseSourceSetRequest(BaseModel):
    source_kind: Literal["direct_url", "results_page"]
    source_url: str = Field(min_length=1, max_length=2000)
    source_title: str | None = Field(default=None, max_length=500)
    expected_version: int = Field(ge=0)


class PaperPermissionApproveRequest(BaseModel):
    expected_expectation_version: int = Field(ge=1)
    max_position_value_usd: float = Field(gt=0, allow_inf_nan=False)


def _require_valid_tracked_event_id(event_id: str) -> str:
    if _POSTGRES_UUID_TEXT.fullmatch(event_id) is None:
        raise HTTPException(status_code=400, detail="event_id must be a valid UUID")
    return event_id


def _require_actor(actor: str | None) -> str:
    canonical_actor = actor.strip() if actor is not None else ""
    if not canonical_actor:
        raise HTTPException(status_code=422, detail="X-MarketAI-Actor is required")
    if len(canonical_actor) > 200:
        raise HTTPException(status_code=422, detail="X-MarketAI-Actor is too long")
    return canonical_actor


def _tracked_event_is_active(event: PersistentTrackedEvent, *, now: datetime) -> bool:
    """Mirror the repository's active/history read-model boundary for one exact row."""
    status = event.status.value
    if status in _ALWAYS_ACTIVE_TRACKED_EVENT_STATUSES:
        return True
    if status not in _TERMINAL_TRACKED_EVENT_STATUSES or event.updated_at is None:
        return False
    return event.updated_at >= now - _TRACKED_EVENT_ACTIVE_HISTORY_WINDOW


def _parse_occurrence_id(value: str) -> tuple[str, str]:
    prefix, separator, raw_id = value.partition(":")
    if separator != ":" or prefix not in {"tracked", "calendar"}:
        raise HTTPException(
            status_code=422,
            detail="occurrence_ids must contain tracked:<uuid> or calendar:<uuid>",
        )
    if _POSTGRES_UUID_TEXT.fullmatch(raw_id) is None:
        raise HTTPException(
            status_code=422,
            detail="occurrence_ids must contain tracked:<uuid> or calendar:<uuid>",
        )
    return prefix, str(UUID(raw_id))


def _iso_or_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    text = str(value).strip()
    return text or None


def _task_position_cap(task_row: dict[str, object] | None) -> float | None:
    if task_row is None:
        return None
    raw_cap = task_row.get("max_position_value_usd")
    return float(raw_cap) if raw_cap is not None else None


def _paper_permission_payload(
    event: PersistentTrackedEvent,
    *,
    current_expectation_version: int,
    task_row: dict[str, object] | None,
) -> dict[str, object]:
    source_event_id = canonical_release_event_id(event)
    if task_row is None:
        return {
            "event_id": event.event_id,
            "source_event_id": source_event_id,
            "instrument": event.instrument,
            "mode": TradingMode.PAPER.value,
            "state": "not_created",
            "task_id": None,
            "approved_by": None,
            "approved_at": None,
            "approved_expectation_version": None,
            "current_expectation_version": current_expectation_version,
            "max_position_value_usd": None,
            "approval_current": False,
        }

    state = str(task_row.get("state") or "")
    raw_approved_version = task_row.get("approved_expectation_version")
    approved_version = int(raw_approved_version) if raw_approved_version is not None else None
    approval_current = (
        state == TradingTaskState.APPROVED.value
        and approved_version == current_expectation_version
    )
    return {
        "event_id": event.event_id,
        "source_event_id": source_event_id,
        "instrument": event.instrument,
        "mode": TradingMode.PAPER.value,
        "state": state,
        "task_id": str(task_row.get("id") or "") or None,
        "approved_by": str(task_row.get("approved_by") or "") or None,
        "approved_at": _iso_or_none(task_row.get("approved_at")),
        "approved_expectation_version": approved_version,
        "current_expectation_version": current_expectation_version,
        "max_position_value_usd": _task_position_cap(task_row),
        "approval_current": approval_current,
    }


def _is_paper_permission_conflict(exc: Exception) -> bool:
    text = str(exc)
    return any(
        token in text
        for token in (
            "trading_task_expectation_version_changed",
            "trading_task_expectation_not_found",
            "trading_task_event_identity_mismatch",
            "trading_task_instrument_mismatch",
            "trading_task_tracked_event_not_found",
        )
    )


def build_tracked_event_release_source_router(
    *,
    require_read: Callable[[str | None], None],
    require_control: Callable[[str | None], None],
    get_tracked_event_repository: Callable[[], TrackedEventRepository],
    get_official_release_source_repository: Callable[[], OfficialReleaseSourceRepository],
    get_trading_task_repository: Callable[[], SupabaseTradingTaskRepository] | None = None,
    get_event_expectation_repository: Callable[[], SupabaseEventExpectationRepository] | None = None,
) -> APIRouter:
    router = APIRouter()
    trading_task_repository_factory = (
        get_trading_task_repository or SupabaseTradingTaskRepository.from_env
    )
    event_expectation_repository_factory = (
        get_event_expectation_repository or SupabaseEventExpectationRepository.from_env
    )

    def load_paper_permission_context(
        event_id: str,
    ) -> tuple[PersistentTrackedEvent, int, SupabaseTradingTaskRepository, dict[str, object] | None]:
        try:
            event = get_tracked_event_repository().get(event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Tracked-event read failed") from exc
        if event is None:
            raise HTTPException(status_code=404, detail="Tracked event not found")

        source_event_id = canonical_release_event_id(event)
        try:
            expectation = event_expectation_repository_factory().get(source_event_id)
            if expectation is None:
                raise HTTPException(status_code=409, detail="Current event expectation is not available")
            if expectation.instrument.strip().upper() != event.instrument.strip().upper():
                raise HTTPException(status_code=409, detail="Tracked event and expectation instrument differ")
            tasks = trading_task_repository_factory()
            task_row = tasks.get_active_row_for_event_mode(
                tracked_event_id=event.event_id,
                mode=TradingMode.PAPER,
            )
        except HTTPException:
            raise
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="PAPER permission read failed") from exc

        return event, expectation.version, tasks, task_row

    @router.get("/api/v1/tracked-events/activity")
    def get_tracked_event_activity_batch(
        occurrence_ids: str = Query(min_length=1, max_length=4000),
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> dict[str, object]:
        require_read(x_marketai_key)
        canonical_ids = tuple(
            dict.fromkeys(
                part.strip() for part in occurrence_ids.split(",") if part.strip()
            )
        )
        if not canonical_ids or len(canonical_ids) > _ACTIVITY_BATCH_MAX_IDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    "occurrence_ids must contain between 1 and "
                    f"{_ACTIVITY_BATCH_MAX_IDS} values"
                ),
            )

        parsed = {
            occurrence_id: _parse_occurrence_id(occurrence_id)
            for occurrence_id in canonical_ids
        }
        tracked_ids = tuple(
            canonical_uuid for prefix, canonical_uuid in parsed.values() if prefix == "tracked"
        )
        calendar_ids = tuple(
            canonical_uuid for prefix, canonical_uuid in parsed.values() if prefix == "calendar"
        )

        try:
            events = get_tracked_event_repository().get_by_occurrences(
                event_ids=tracked_ids,
                calendar_event_ids=calendar_ids,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Tracked-event activity read failed"
            ) from exc

        by_tracked_id = {event.event_id: event for event in events}
        by_calendar_id = {
            event.calendar_event_id: event
            for event in events
            if event.calendar_event_id is not None
        }
        now = datetime.now(UTC)
        items: list[dict[str, object]] = []
        for occurrence_id in canonical_ids:
            prefix, canonical_uuid = parsed[occurrence_id]
            event = (
                by_tracked_id.get(canonical_uuid)
                if prefix == "tracked"
                else by_calendar_id.get(canonical_uuid)
            )
            items.append(
                {
                    "occurrence_id": occurrence_id,
                    "exists": event is not None,
                    "active": event is not None
                    and _tracked_event_is_active(event, now=now),
                }
            )
        return {"items": items}

    @router.get("/api/v1/tracked-events/{event_id}/activity")
    def get_tracked_event_activity(
        event_id: str,
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> dict[str, object]:
        require_read(x_marketai_key)
        event_id = _require_valid_tracked_event_id(event_id)
        try:
            event = get_tracked_event_repository().get(event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Tracked-event read failed") from exc

        if event is None:
            return {"event_id": event_id, "exists": False, "active": False}

        return {
            "event_id": event_id,
            "exists": True,
            "active": _tracked_event_is_active(event, now=datetime.now(UTC)),
        }

    @router.get("/api/v1/tracked-events/{event_id}/paper-permission")
    def get_tracked_event_paper_permission(
        event_id: str,
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> dict[str, object]:
        require_read(x_marketai_key)
        event_id = _require_valid_tracked_event_id(event_id)
        event, current_version, _, task_row = load_paper_permission_context(event_id)
        return _paper_permission_payload(
            event,
            current_expectation_version=current_version,
            task_row=task_row,
        )

    @router.post("/api/v1/tracked-events/{event_id}/paper-permission/approve")
    def approve_tracked_event_paper_permission(
        event_id: str,
        request: PaperPermissionApproveRequest,
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
        x_marketai_actor: str | None = Header(default=None, alias="X-MarketAI-Actor"),
    ) -> dict[str, object]:
        require_control(x_marketai_control_key)
        actor = _require_actor(x_marketai_actor)
        event_id = _require_valid_tracked_event_id(event_id)
        event, current_version, tasks, _ = load_paper_permission_context(event_id)

        # Reject the visibly stale request before entering the write boundary.
        # The DB RPC repeats the same check under the per-event advisory lock,
        # so a version change after this read still rolls back the whole action.
        if request.expected_expectation_version != current_version:
            raise HTTPException(
                status_code=409,
                detail="Event expectation changed; review the current version before approving",
            )

        try:
            approved_row = tasks.approve_paper_permission(
                tracked_event_id=event.event_id,
                source_event_id=canonical_release_event_id(event),
                instrument=event.instrument,
                actor=actor,
                expected_expectation_version=request.expected_expectation_version,
                max_position_value_usd=request.max_position_value_usd,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            if _is_paper_permission_conflict(exc):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise HTTPException(status_code=503, detail="PAPER permission approval failed") from exc

        return _paper_permission_payload(
            event,
            current_expectation_version=request.expected_expectation_version,
            task_row=approved_row,
        )

    @router.put("/api/v1/tracked-events/{event_id}/release-source")
    def set_tracked_event_release_source(
        event_id: str,
        request: OfficialReleaseSourceSetRequest,
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
        x_marketai_actor: str | None = Header(default=None, alias="X-MarketAI-Actor"),
    ) -> dict[str, object]:
        require_control(x_marketai_control_key)
        actor = _require_actor(x_marketai_actor)
        event_id = _require_valid_tracked_event_id(event_id)
        try:
            event = get_tracked_event_repository().get(event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Tracked-event read failed") from exc
        if event is None:
            raise HTTPException(status_code=404, detail="Tracked event not found")

        release_event_id = canonical_release_event_id(event)
        try:
            source = OfficialReleaseSource(
                event_id=release_event_id,
                source_kind=request.source_kind,
                source_url=request.source_url,
                source_title=request.source_title,
            )
            saved = get_official_release_source_repository().set(
                source,
                expected_version=request.expected_version,
                actor=actor,
            )
            if saved.version is None:
                raise RuntimeError("official release source write returned no version")
            model = build_tracked_event_release_source_read_model(
                event,
                OfficialReleaseSourceState(source=saved, version=saved.version),
            )
        except OfficialReleaseSourceVersionConflict as exc:
            raise HTTPException(
                status_code=409, detail="Official release source version conflict"
            ) from exc
        except OfficialReleaseSourceEventNotFound as exc:
            raise HTTPException(status_code=404, detail="Tracked event not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Tracked-event release source write failed",
            ) from exc

        return asdict(model)

    @router.get("/api/v1/tracked-events/{event_id}/release-source")
    def get_tracked_event_release_source(
        event_id: str,
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> dict[str, object]:
        require_read(x_marketai_key)
        event_id = _require_valid_tracked_event_id(event_id)
        try:
            event = get_tracked_event_repository().get(event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Tracked-event read failed") from exc
        if event is None:
            raise HTTPException(status_code=404, detail="Tracked event not found")

        release_event_id = canonical_release_event_id(event)
        try:
            state = get_official_release_source_repository().get_state(release_event_id)
            model = build_tracked_event_release_source_read_model(event, state)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Tracked-event release source read failed",
            ) from exc

        return asdict(model)

    return router
