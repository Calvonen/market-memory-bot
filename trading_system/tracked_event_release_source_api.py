from __future__ import annotations

import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal, Protocol

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceEventNotFound,
    OfficialReleaseSourceState,
    OfficialReleaseSourceVersionConflict,
)
from trading_system.tracked_event_release_source import (
    build_tracked_event_release_source_read_model,
)
from trading_system.tracked_event_repository import PersistentTrackedEvent
from trading_system.workflow_readiness_evidence_loader import canonical_release_event_id


_POSTGRES_UUID_TEXT = re.compile(
    r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
_TRACKED_EVENT_ACTIVE_HISTORY_WINDOW = timedelta(hours=24)
_ALWAYS_ACTIVE_TRACKED_EVENT_STATUSES = {"tracked", "monitoring"}
_TERMINAL_TRACKED_EVENT_STATUSES = {"completed", "cancelled", "failed"}


class TrackedEventRepository(Protocol):
    def get(self, event_id: str) -> PersistentTrackedEvent | None: ...


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


def build_tracked_event_release_source_router(
    *,
    require_read: Callable[[str | None], None],
    require_control: Callable[[str | None], None],
    get_tracked_event_repository: Callable[[], TrackedEventRepository],
    get_official_release_source_repository: Callable[[], OfficialReleaseSourceRepository],
) -> APIRouter:
    router = APIRouter()

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
            # The caller uses this read model to distinguish an archived/orphan
            # expectation shell from an active canonical event. Missing is a
            # normal negative lookup here, not a navigation-style 404.
            return {"event_id": event_id, "exists": False, "active": False}

        return {
            "event_id": event_id,
            "exists": True,
            "active": _tracked_event_is_active(event, now=datetime.now(UTC)),
        }

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
