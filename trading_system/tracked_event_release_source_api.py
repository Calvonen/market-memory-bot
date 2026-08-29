from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Protocol

from fastapi import APIRouter, Header, HTTPException

from trading_system.official_release_source_repository import OfficialReleaseSourceState
from trading_system.tracked_event_release_source import (
    build_tracked_event_release_source_read_model,
)
from trading_system.tracked_event_repository import PersistentTrackedEvent
from trading_system.workflow_readiness_evidence_loader import canonical_release_event_id


class TrackedEventRepository(Protocol):
    def get(self, event_id: str) -> PersistentTrackedEvent | None: ...


class OfficialReleaseSourceRepository(Protocol):
    def get_state(self, event_id: str) -> OfficialReleaseSourceState: ...


def build_tracked_event_release_source_router(
    *,
    require_read: Callable[[str | None], None],
    get_tracked_event_repository: Callable[[], TrackedEventRepository],
    get_official_release_source_repository: Callable[[], OfficialReleaseSourceRepository],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/tracked-events/{event_id}/release-source")
    def get_tracked_event_release_source(
        event_id: str,
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> dict[str, object]:
        require_read(x_marketai_key)
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
