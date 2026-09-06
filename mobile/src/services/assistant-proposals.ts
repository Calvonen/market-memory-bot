import { apiControlPost, apiGet } from '@/services/api';

export type AssistantProposalStatus =
  | 'draft'
  | 'ready_for_review'
  | 'rejected'
  | 'approved_for_materialization'
  | 'materialized';

export type AssistantProposalPayload = {
  company_name: string;
  instrument: string;
  market: string;
  kind: 'earnings';
  title: string;
  scheduled_date: string;
  event_at: string;
  event_time_status: 'confirmed' | 'estimated' | 'unknown';
  base_expectation_version: number;
  official_source: {
    source_kind: 'direct_url' | 'results_page';
    source_url: string;
    source_title?: string | null;
  };
  strategy: Record<string, unknown>;
};

export type AssistantProposalMaterialization = {
  event_id: string;
  tracked_event_id: string;
  expectation_version: number;
};

export type AssistantProposal = {
  id: string;
  proposal_key: string;
  payload: AssistantProposalPayload;
  requested_execution_mode: 'demo' | 'live';
  status: AssistantProposalStatus;
  reviewed_by: string | null;
  review_round: number;
  created_at: string;
  updated_at: string;
  materialization: AssistantProposalMaterialization | null;
};

export type AssistantPreparationApprovalResult = {
  proposal_id: string;
  status: 'materialized';
  review_round: number;
  event_id: string;
  tracked_event_id: string;
  expectation_version: number;
  retried: boolean;
  trading_authority_granted: false;
};

export function getAssistantProposals(): Promise<AssistantProposal[]> {
  return apiGet<AssistantProposal[]>('/api/v1/assistant-proposals');
}

export function getAssistantProposal(proposalId: string): Promise<AssistantProposal> {
  return apiGet<AssistantProposal>(
    `/api/v1/assistant-proposals/${encodeURIComponent(proposalId)}`,
  );
}

export function approveAssistantProposalPreparation(
  proposalId: string,
  reviewer: string,
  expectedReviewRound: number,
): Promise<AssistantPreparationApprovalResult> {
  return apiControlPost<AssistantPreparationApprovalResult>(
    `/api/v1/assistant-proposals/${encodeURIComponent(proposalId)}/approve-preparation`,
    { reviewer, expected_review_round: expectedReviewRound },
  );
}
