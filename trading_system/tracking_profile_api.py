from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from trading_system.tracking_profile_registry import (
    TrackedInstrumentProfileInstrumentNotFound,
)


TrackingProfileLiteral = Literal["earnings", "trend", "future_tech"]


class TrackedInstrumentProfileRequest(BaseModel):
    specs: str = Field(default="", max_length=4000)
    enabled: bool = True

    @field_validator("specs", mode="before")
    @classmethod
    def _strip_specs(cls, value):
        return value.strip() if isinstance(value, str) else value


def _require_actor(value: str | None) -> str:
    actor = (value or "").strip()
    if not actor or len(actor) > 200:
        raise HTTPException(
            status_code=422,
            detail="X-MarketAI-Actor must be nonblank and at most 200 characters",
        )
    return actor


def build_tracking_profile_router(
    *,
    require_read: Callable[[str | None], None],
    require_control: Callable[[str | None], None],
    get_tracking_profile_registry,
) -> APIRouter:
    """Read/write tracked-instrument profile configuration only."""

    router = APIRouter()

    @router.get("/api/v1/tracked-instruments/{tracked_instrument_id}/profiles")
    def list_tracking_profiles(
        tracked_instrument_id: str,
        x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
    ) -> list[dict]:
        require_read(x_marketai_key)
        try:
            return [
                asdict(record)
                for record in get_tracking_profile_registry().list_for_instrument(
                    tracked_instrument_id
                )
            ]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Tracked instrument profile read failed"
            ) from exc

    @router.put(
        "/api/v1/tracked-instruments/{tracked_instrument_id}/profiles/{profile_type}"
    )
    def set_tracking_profile(
        tracked_instrument_id: str,
        profile_type: TrackingProfileLiteral,
        request: TrackedInstrumentProfileRequest,
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
        x_marketai_actor: str | None = Header(default=None, alias="X-MarketAI-Actor"),
    ) -> dict:
        require_control(x_marketai_control_key)
        actor = _require_actor(x_marketai_actor)
        try:
            record = get_tracking_profile_registry().upsert(
                tracked_instrument_id=tracked_instrument_id,
                profile_type=profile_type,
                specs=request.specs,
                enabled=request.enabled,
                actor=actor,
            )
            return asdict(record)
        except TrackedInstrumentProfileInstrumentNotFound as exc:
            raise HTTPException(
                status_code=404, detail="Tracked instrument not found"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Tracked instrument profile persistence failed"
            ) from exc

    return router
