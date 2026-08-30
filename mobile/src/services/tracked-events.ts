import { apiControlPost, apiGet, apiPut } from '@/services/api';

export const TRACKED_EVENT_RELEASE_SKIP_REASON_MAX_LENGTH = 1000;

export type TrackedEventMonitoringStageSnapshot = {
  start_after_minutes: number;
  interval_minutes: number;
};

// Immutable record of the effective reaction-monitoring settings a given
// event was actually tracked with (trading_system/tracked_event_config.py).
// Only schema_version 1 is understood by the UI today - render it as-is,
// never re-derive it from today's live/global settings.
export type TrackedEventTrackingConfigSnapshot = {
  schema_version: number;
  monitor_hours: number;
  reference_lead_seconds: number;
  max_wait_for_market_hours: number;
  reaction_stages: TrackedEventMonitoringStageSnapshot[];
};

export type TrackedEventLatestReaction = {
  interval_minutes: number;
  candle_start: string;
  reference_price: string;
  close_price: string;
  return_pct: string;
  direction: string;
  evolution: string;
  observed_at: string;
};

export type TrackedEventLatestReactionResponse = {
  event_id: string;
  latest_reaction: TrackedEventLatestReaction | null;
};

export type TrackedEventWorkflowStepStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'skipped'
  | 'failed'
  | 'action_required';

export type TrackedEventWorkflowStepMode = 'required' | 'optional' | 'skip';

export type TrackedEventWorkflowStep = {
  key: string;
  mode: TrackedEventWorkflowStepMode;
  status: TrackedEventWorkflowStepStatus;
  action_target: string | null;
  action_code: string | null;
  action_reason: string | null;
};

export type TrackedEventWorkflowResponse = {
  event_id: string;
  profile_id: string;
  trading_mode: 'PAPER' | 'LIVE' | null;
  steps: TrackedEventWorkflowStep[];
};

export type TrackedEventReleaseSource = {
  event_id: string;
  release_event_id: string;
  active: boolean;
  version: number;
  source_kind: 'direct_url' | 'results_page' | null;
  source_url: string | null;
  source_title: string | null;
};

export type PutTrackedEventReleaseSourceInput = {
  source_kind: 'direct_url' | 'results_page';
  source_url: string;
  source_title?: string;
  expected_version: number;
};

export type TrackedEventReleaseIngestionResult = {
  event_id: string;
  release_event_id: string;
  status: string;
  source_document_id: string | null;
  analysis_id: string | null;
  message: string | null;
  overdue: boolean;
};

export type TrackedEventReleaseSkipResult = {
  event_id: string;
  release_event_id: string;
  status: 'skipped';
};

export type TrackedMarketEvent = {
  event_id: string;
  tracked_instrument_id: string;
  calendar_event_id: string | null;
  company_name: string;
  instrument: string;
  market: string;
  source: string;
  external_key: string;
  kind: string;
  title: string;
  event_at: string;
  event_time_status: 'confirmed' | 'estimated' | 'unknown';
  status: 'tracked' | 'monitoring' | 'completed' | 'cancelled' | 'failed';
  resolved_etoro_instrument_id: number | null;
  resolved_etoro_symbol: string | null;
  resolved_etoro_display_name: string | null;
  resolution_armed_at: string | null;
  resolution_armed_by: string | null;
  reference_price: string | null;
  reference_captured_at: string | null;
  reference_kind: string | null;
  reaction_anchor_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  last_error: string | null;
  updated_at: string | null;
  tracking_config_snapshot: TrackedEventTrackingConfigSnapshot | null;
};

export type TrackedEventView = 'active' | 'history';

export function getTrackedEvents(
  view: TrackedEventView = 'active',
  limit = 20,
): Promise<TrackedMarketEvent[]> {
  return apiGet<TrackedMarketEvent[]>(
    `/api/v1/tracked-events?view=${encodeURIComponent(view)}&limit=${encodeURIComponent(String(limit))}`,
  );
}

export function getTrackedEventLatestReaction(
  eventId: string,
): Promise<TrackedEventLatestReactionResponse> {
  return apiGet<TrackedEventLatestReactionResponse>(
    `/api/v1/tracked-events/${encodeURIComponent(eventId)}/latest-reaction`,
  );
}

export function getTrackedEventWorkflow(eventId: string): Promise<TrackedEventWorkflowResponse> {
  return apiGet<TrackedEventWorkflowResponse>(
    `/api/v1/tracked-events/${encodeURIComponent(eventId)}/workflow`,
  );
}

export function getTrackedEventReleaseSource(eventId: string): Promise<TrackedEventReleaseSource> {
  return apiGet<TrackedEventReleaseSource>(
    `/api/v1/tracked-events/${encodeURIComponent(eventId)}/release-source`,
  );
}

export function putTrackedEventReleaseSource(
  eventId: string,
  input: PutTrackedEventReleaseSourceInput,
  actor: string,
): Promise<TrackedEventReleaseSource> {
  return apiPut<TrackedEventReleaseSource>(
    `/api/v1/tracked-events/${encodeURIComponent(eventId)}/release-source`,
    input,
    { 'X-MarketAI-Actor': actor },
  );
}

export function ingestTrackedEventRelease(
  eventId: string,
  actor: string,
): Promise<TrackedEventReleaseIngestionResult> {
  return apiControlPost<TrackedEventReleaseIngestionResult>(
    `/api/v1/tracked-events/${encodeURIComponent(eventId)}/release-ingestion`,
    undefined,
    { 'X-MarketAI-Actor': actor },
  );
}

export function skipTrackedEventRelease(
  eventId: string,
  actor: string,
  reason: string,
): Promise<TrackedEventReleaseSkipResult> {
  const normalizedReason = reason.trim();
  if (!normalizedReason) {
    return Promise.reject(new Error('Julkaisun ohituksen syy puuttuu.'));
  }
  if (normalizedReason.length > TRACKED_EVENT_RELEASE_SKIP_REASON_MAX_LENGTH) {
    return Promise.reject(
      new Error(`Julkaisun ohituksen syy saa olla enintään ${TRACKED_EVENT_RELEASE_SKIP_REASON_MAX_LENGTH} merkkiä.`),
    );
  }
  return apiControlPost<TrackedEventReleaseSkipResult>(
    `/api/v1/tracked-events/${encodeURIComponent(eventId)}/release-skip`,
    { reason: normalizedReason },
    { 'X-MarketAI-Actor': actor },
  );
}
