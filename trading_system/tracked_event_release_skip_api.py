from __future__ import annotations

import re
from dataclasses import asdict
from typing import Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from trading_system.tracked_event_release_skip import (
    MAX_RELEASE_SKIP_REASON_LENGTH,
    ReleaseSkipConflict,
    skip_tracked_event_release,
)

_UUID = re.compile(
    r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})"
)


class ReleaseSkipRequest(BaseModel):
    reason: str


def build_tracked_event_release_skip_router(
    *,
    require_control: Callable[[str | None], None],
    get_tracked_event_repository,
    get_release_skip_audit_repository,
    get_release_shell_repository=None,
) -> APIRouter:
    # get_release_shell_repository remains an accepted wiring argument for
    # create_app compatibility, but is deliberately never called here. The
    # shell "ensure" repository is mutating; skip is audit-only and delegates
    # atomic read-only binding validation to record_tracked_event_release_skip.
    del get_release_shell_repository
    router = APIRouter()

    @router.post("/api/v1/tracked-events/{event_id}/release-skip")
    def skip_release(
        event_id: str,
        request: ReleaseSkipRequest,
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
        x_marketai_actor: str | None = Header(default=None, alias="X-MarketAI-Actor"),
    ) -> dict[str, str]:
        require_control(x_marketai_control_key)
        actor = (x_marketai_actor or "").strip()
        if not actor or len(actor) > 200:
            raise HTTPException(
                status_code=422,
                detail="X-MarketAI-Actor must be nonblank and at most 200 characters",
            )
        reason = request.reason.strip()
        if not reason or len(reason) > MAX_RELEASE_SKIP_REASON_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"reason must be nonblank and at most {MAX_RELEASE_SKIP_REASON_LENGTH} characters",
            )
        if _UUID.fullmatch(event_id) is None:
            raise HTTPException(status_code=400, detail="event_id must be a valid UUID")
        try:
            event = get_tracked_event_repository().get(event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="Tracked event not found")
            return asdict(
                skip_tracked_event_release(
                    event,
                    audit_repository=get_release_skip_audit_repository(),
                    actor=actor,
                    reason=reason,
                )
            )
        except HTTPException:
            raise
        except ReleaseSkipConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Release skip audit failed"
            ) from exc

    return router
