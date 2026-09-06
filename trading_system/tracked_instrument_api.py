from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from trading_system.assistant_proposal_api import build_assistant_proposal_router
from trading_system.assistant_proposal_approval_service import (
    AssistantProposalApprovalService,
    build_default_assistant_proposal_approval_service,
)


class TrackInstrumentRequest(BaseModel):
    instrument: str = Field(min_length=1, max_length=80)
    company_name: str = Field(default="", max_length=200)
    market: str = Field(default="", max_length=100)
    source: Literal["scanner", "calendar", "manual"]

    @field_validator("instrument", "company_name", "market", mode="before")
    @classmethod
    def _strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


def _require_actor(value: str | None) -> str:
    actor = (value or "").strip()
    if not actor or len(actor) > 200:
        raise HTTPException(
            status_code=422,
            detail="X-MarketAI-Actor must be nonblank and at most 200 characters",
        )
    return actor


def build_tracked_instrument_router(
    *,
    require_control: Callable[[str | None], None],
    get_tracked_instrument_registry,
    require_read: Callable[[str | None], None] | None = None,
) -> APIRouter:
    router = APIRouter()
    assistant_approval_service: AssistantProposalApprovalService | None = None

    def get_assistant_approval_service() -> AssistantProposalApprovalService:
        nonlocal assistant_approval_service
        if assistant_approval_service is None:
            assistant_approval_service = build_default_assistant_proposal_approval_service()
        return assistant_approval_service

    # The assistant proposal endpoint belongs to the same control plane as
    # tracked-instrument mutations. Its service is lazy so ordinary API startup
    # and read-only requests do not require eToro/Supabase materializer config.
    router.include_router(
        build_assistant_proposal_router(
            require_control=require_control,
            get_approval_service=get_assistant_approval_service,
        )
    )

    if require_read is not None:

        @router.get("/api/v1/tracked-instruments")
        def list_tracked_instruments(
            x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
        ) -> list[dict]:
            require_read(x_marketai_key)
            try:
                return [asdict(record) for record in get_tracked_instrument_registry().list_active()]
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail="Tracked instrument read failed"
                ) from exc

    @router.post("/api/v1/tracked-instruments")
    def track_instrument(
        request: TrackInstrumentRequest,
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
        x_marketai_actor: str | None = Header(default=None, alias="X-MarketAI-Actor"),
    ) -> dict:
        require_control(x_marketai_control_key)
        actor = _require_actor(x_marketai_actor)
        try:
            record = get_tracked_instrument_registry().upsert(
                instrument=request.instrument,
                company_name=request.company_name,
                market=request.market,
                source=request.source,
                actor=actor,
            )
            return asdict(record)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Tracked instrument persistence failed"
            ) from exc

    @router.post("/api/v1/tracked-instruments/{tracked_instrument_id}/deactivate")
    def deactivate_tracked_instrument(
        tracked_instrument_id: str,
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
        x_marketai_actor: str | None = Header(default=None, alias="X-MarketAI-Actor"),
    ) -> dict:
        require_control(x_marketai_control_key)
        actor = _require_actor(x_marketai_actor)
        normalized_id = tracked_instrument_id.strip()
        if not normalized_id:
            raise HTTPException(status_code=422, detail="Tracked instrument id must be nonblank")
        try:
            record = get_tracked_instrument_registry().deactivate(
                tracked_instrument_id=normalized_id,
                actor=actor,
            )
            return asdict(record)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Tracked instrument deactivation failed"
            ) from exc

    return router