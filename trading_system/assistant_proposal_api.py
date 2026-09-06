from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from trading_system.assistant_event_proposal import (
    AssistantProposalIdentityConflict,
    AssistantProposalLiveLocked,
    AssistantProposalNotFound,
    AssistantProposalNotReady,
    AssistantProposalPreparationApprovalConflict,
)
from trading_system.assistant_proposal_approval_service import (
    AssistantProposalApprovalService,
    MAX_PREPARATION_REVIEWER_LENGTH,
)
from trading_system.assistant_proposal_materializer import (
    AssistantProposalMaterializationError,
)
from trading_system.assistant_proposal_read_repository import (
    ALL_STATUSES,
    SupabaseAssistantProposalReadRepository,
)

AssistantProposalStatus = Literal[
    "draft",
    "ready_for_review",
    "rejected",
    "approved_for_materialization",
    "materialized",
]


class AssistantPreparationApprovalRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=MAX_PREPARATION_REVIEWER_LENGTH)
    expected_review_round: int = Field(ge=0)

    @field_validator("reviewer", mode="before")
    @classmethod
    def _strip_reviewer(cls, value):
        return value.strip() if isinstance(value, str) else value


def _canonical_proposal_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="Assistant proposal id must be a UUID") from exc


def build_assistant_proposal_router(
    *,
    require_control: Callable[[str | None], None],
    get_approval_service: Callable[[], AssistantProposalApprovalService],
    require_read: Callable[[str | None], None] | None = None,
    get_read_repository: Callable[[], SupabaseAssistantProposalReadRepository] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/assistant-proposals", tags=["assistant-proposals"])

    if require_read is not None and get_read_repository is not None:

        @router.get("")
        def list_proposals(
            proposal_status: AssistantProposalStatus | None = Query(default=None, alias="status"),
            limit: int = Query(default=50, ge=1, le=100),
            x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
        ) -> list[dict]:
            require_read(x_marketai_key)
            try:
                if proposal_status is not None and proposal_status not in ALL_STATUSES:
                    raise HTTPException(status_code=422, detail="Invalid assistant proposal status")
                return [asdict(record) for record in get_read_repository().list(status=proposal_status, limit=limit)]
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Assistant proposal read failed") from exc

        @router.get("/{proposal_id}")
        def get_proposal(
            proposal_id: str,
            x_marketai_key: str | None = Header(default=None, alias="X-MarketAI-Key"),
        ) -> dict:
            require_read(x_marketai_key)
            canonical_id = _canonical_proposal_id(proposal_id)
            try:
                record = get_read_repository().get(canonical_id)
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Assistant proposal read failed") from exc
            if record is None:
                raise HTTPException(status_code=404, detail="Assistant proposal not found")
            return asdict(record)

    @router.post("/{proposal_id}/approve-preparation")
    def approve_preparation(
        proposal_id: str,
        request: AssistantPreparationApprovalRequest,
        x_marketai_control_key: str | None = Header(default=None, alias="X-MarketAI-Control-Key"),
    ) -> dict:
        require_control(x_marketai_control_key)
        try:
            result = get_approval_service().approve_preparation(
                proposal_id,
                reviewer=request.reviewer,
                expected_review_round=request.expected_review_round,
            )
        except AssistantProposalNotFound as exc:
            raise HTTPException(status_code=404, detail="Assistant proposal not found") from exc
        except (
            AssistantProposalPreparationApprovalConflict,
            AssistantProposalLiveLocked,
            AssistantProposalNotReady,
            AssistantProposalIdentityConflict,
            AssistantProposalMaterializationError,
        ) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

        payload = asdict(result)
        payload["trading_authority_granted"] = False
        return payload

    return router
