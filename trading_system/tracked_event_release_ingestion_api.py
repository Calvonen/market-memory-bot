from __future__ import annotations

import re
from dataclasses import asdict
from typing import Callable

from fastapi import APIRouter, Header, HTTPException

from trading_system.tracked_event_release_ingestion import (
    ReleaseIngestionNotReady,
    ingest_tracked_event_release_once,
)

_UUID = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})")


def build_tracked_event_release_ingestion_router(
    *, require_control: Callable[[str | None], None], get_tracked_event_repository,
    get_expectation_repository, get_official_release_source_repository,
    get_release_repository, get_release_shell_repository,
    get_ingestion_audit_repository, get_event_analyzer,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/tracked-events/{event_id}/release-ingestion")
    def ingest_release(
        event_id: str,
        x_marketai_control_key: str | None = Header(default=None, alias="X-MarketAI-Control-Key"),
        x_marketai_actor: str | None = Header(default=None, alias="X-MarketAI-Actor"),
    ) -> dict[str, object]:
        require_control(x_marketai_control_key)
        actor = (x_marketai_actor or "").strip()
        if not actor or len(actor) > 200:
            raise HTTPException(status_code=422, detail="X-MarketAI-Actor must be nonblank and at most 200 characters")
        if _UUID.fullmatch(event_id) is None:
            raise HTTPException(status_code=400, detail="event_id must be a valid UUID")
        try:
            event = get_tracked_event_repository().get(event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="Tracked event not found")
            result = ingest_tracked_event_release_once(
                event,
                expectation_repository=get_expectation_repository(),
                official_release_source_repository=get_official_release_source_repository(),
                release_repository=get_release_repository(),
                release_shell_repository=get_release_shell_repository(),
                ingestion_audit_repository=get_ingestion_audit_repository(),
                analyzer_factory=get_event_analyzer,
                actor=actor,
            )
            return asdict(result)
        except HTTPException:
            raise
        except ReleaseIngestionNotReady as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Release ingestion attempt failed") from exc

    return router
