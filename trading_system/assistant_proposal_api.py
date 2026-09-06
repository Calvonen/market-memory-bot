from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from trading_system.assistant_event_proposal import (
    AssistantProposalNotFound,
    AssistantProposalPreparationApprovalConflict,
)
from trading_system.assistant_proposal_approval_service import (
    AssistantProposalApprovalService,
)
from trading_system.assistant_proposal_materializer import (
    AssistantProposalMaterializationError,
)


class AssistantPreparationApprovalRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=200)

    @field_validator("reviewer", mode="before")
    @classmethod
    def _strip_reviewer(cls, value):
        return value.strip() if isinstance(value, str) else value


def build_assistant_proposal_router(
    *,
    require_control: Callable[[str | None], None],
    get_approval_service: Callable[[], AssistantProposalApprovalService],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/assistant-proposals", tags=["assistant-proposals"])

    @router.post("/{proposal_id}/approve-preparation")
    def approve_preparation(
        proposal_id: str,
        request: AssistantPreparationApprovalRequest,
        x_marketai_control_key: str | None = Header(
            default=None, alias="X-MarketAI-Control-Key"
        ),
    ) -> dict:
        require_control(x_marketai_control_key)
        try:
            result = get_approval_service().approve_preparation(
                proposal_id, reviewer=request.reviewer
            )
        except AssistantProposalNotFound as exc:
            raise HTTPException(status_code=404, detail="Assistant proposal not found") from exc
        except AssistantProposalPreparationApprovalConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AssistantProposalMaterializationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        payload = asdict(result)
        payload["trading_authority_granted"] = False
        return payload

    return router
