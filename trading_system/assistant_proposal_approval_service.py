from __future__ import annotations

from dataclasses import dataclass

from trading_system.assistant_event_proposal import (
    AssistantProposalNotFound,
    AssistantProposalPreparationApprovalConflict,
    SupabaseAssistantEventProposalRepository,
)
from trading_system.assistant_proposal_materializer import (
    AssistantProposalMaterializationError,
    AssistantProposalMaterializationResult,
    AssistantProposalMaterializer,
)
from trading_system.assistant_strategy_draft_repository import (
    SupabaseAssistantStrategyDraftApprovalRepository,
)
from trading_system.calendar_release_worker import SupabaseCalendarReleaseTargetRepository
from trading_system.canonical_tracked_event_ingress import SupabaseCanonicalTrackedEventIngress
from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.official_release_source_repository import (
    SupabaseOfficialReleaseSourceRepository,
)
from trading_system.supabase_event_repository import SupabaseEventExpectationRepository
from trading_system.tracked_instrument_registry import SupabaseTrackedInstrumentRegistry


@dataclass(frozen=True)
class AssistantPreparationApprovalResult:
    proposal_id: str
    status: str
    review_round: int
    event_id: str
    tracked_event_id: str
    expectation_version: int
    retried: bool


class AssistantProposalApprovalService:
    """Approve preparation and materialize it, without granting trading authority."""

    def __init__(
        self,
        *,
        proposals: SupabaseAssistantEventProposalRepository,
        materializer: AssistantProposalMaterializer,
    ) -> None:
        self.proposals = proposals
        self.materializer = materializer

    def approve_preparation(
        self, proposal_id: str, *, reviewer: str
    ) -> AssistantPreparationApprovalResult:
        status, review_round = self.proposals.approve_for_materialization(
            proposal_id, reviewer=reviewer
        )
        result: AssistantProposalMaterializationResult = self.materializer.materialize(
            proposal_id
        )
        return AssistantPreparationApprovalResult(
            proposal_id=proposal_id,
            status="materialized",
            review_round=review_round,
            event_id=result.event_id,
            tracked_event_id=result.tracked_event_id,
            expectation_version=result.expectation_version,
            retried=result.retried or status == "materialized",
        )



def build_default_assistant_proposal_approval_service() -> AssistantProposalApprovalService:
    proposals = SupabaseAssistantEventProposalRepository.from_env()
    client = proposals.client
    materializer = AssistantProposalMaterializer(
        proposals=proposals,
        resolver=EtoroInstrumentResolver(EtoroMarketDataProvider.from_env()),
        registry=SupabaseTrackedInstrumentRegistry(client),
        events=SupabaseCanonicalTrackedEventIngress(client),
        release_targets=SupabaseCalendarReleaseTargetRepository(client),
        expectations=SupabaseEventExpectationRepository(client),
        official_sources=SupabaseOfficialReleaseSourceRepository(client),
        approvals=SupabaseAssistantStrategyDraftApprovalRepository(client),
    )
    return AssistantProposalApprovalService(
        proposals=proposals,
        materializer=materializer,
    )


__all__ = [
    "AssistantPreparationApprovalResult",
    "AssistantProposalApprovalService",
    "AssistantProposalMaterializationError",
    "AssistantProposalNotFound",
    "AssistantProposalPreparationApprovalConflict",
    "build_default_assistant_proposal_approval_service",
]
