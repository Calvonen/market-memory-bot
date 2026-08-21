export const API_URL = process.env.EXPO_PUBLIC_MARKETAI_API_URL ?? 'http://127.0.0.1:8001';
const READ_API_KEY = process.env.EXPO_PUBLIC_MARKETAI_READ_API_KEY ?? '';
// Strong write-auth for the strategy-draft approve endpoint only - a
// separate, narrower-scoped credential from READ_API_KEY (which must never
// authorize a write). This is still an MVP-tier shared secret bundled into
// the client, same caveat as READ_API_KEY: it must never be the backend's
// admin token or any Supabase service-role secret - this file must never
// reference either.
const CONTROL_API_KEY = process.env.EXPO_PUBLIC_MARKETAI_CONTROL_API_KEY ?? '';

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json() as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // Keep the HTTP status fallback when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { headers: { 'X-MarketAI-Key': READ_API_KEY } });
  return parseJsonResponse<T>(response);
}

async function apiPost<T>(path: string, body: unknown, authHeader: Record<string, string>): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader },
    body: JSON.stringify(body),
  });
  return parseJsonResponse<T>(response);
}

export type SymbolSuggestion = { ticker: string; name: string; exchange: string };

export type MarketMemoryResult = {
  ticker: string; price: number; trend: string; momentum: string;
  result: { direction: string; average_return_15d: number | null };
  analog_matches: { date: string; type: string; score: number; scores: Record<string, number> }[];
  series: { date: string; close: number }[];
};

export type ScannerResult = { market: string; markets: string[]; partial: boolean; results: {
  ticker: string; price: number; direction: string; best_similarity: number | null; trend: string;
}[] };

// -- Tulosjulkaisut / event tracking ---------------------------------------

export type EventExpectation = {
  event_id: string;
  instrument: string;
  event_name: string;
  scheduled_date: string;
  consensus: Record<string, number | string | null>;
  important_kpis: string[];
  bull_case: string[];
  base_case: string[];
  bear_case: string[];
  triggers: Record<string, number | string>;
  invalidation_conditions: string[];
  source_name: string | null;
  source_url: string | null;
  source_as_of: string | null;
  version: number;
  updated_at: string;
};

export type CompletedComponent = {
  direction?: string;
  score?: number;
  max_score?: number;
};

export type PaperRun = {
  status?: string;
  message?: string;
  // The expectation version this run's analysis/strategy/risk decision was
  // computed against. Compare against the event's current `version` (from
  // getEvent()) before presenting the dashboard as reflecting the latest
  // consensus/triggers - an edited expectation can outdate an existing run.
  expectation_version?: number;
  completed_components?: {
    fundamental?: CompletedComponent;
    catalyst?: CompletedComponent;
  } | null;
  confirmation_deadline_at?: string | null;
  expired_at?: string | null;
  strategy?: {
    direction?: string;
    confidence?: number;
    scores?: {
      fundamental?: number;
      catalyst?: number;
      technical?: number;
      market_memory?: number;
      news_sentiment?: number;
      total?: number;
    };
    long_evidence?: number;
    short_evidence?: number;
  } | null;
  risk?: {
    status?: string;
    reasons?: string[];
    max_risk_amount?: number;
    max_position_value?: number;
    max_quantity?: number;
    reward_risk?: number;
  } | null;
  paper_order?: {
    direction?: string;
    quantity?: number;
    reference_price?: number;
    status?: string;
  } | null;
};

export type PaperStatus = {
  event_id: string;
  instrument: string;
  event_name: string;
  scheduled_date: string;
  expectation_version: number;
  paper_run: PaperRun | null;
  trading_mode: string;
};

export function getEvents(): Promise<EventExpectation[]> {
  return apiGet<EventExpectation[]>('/api/v1/events');
}

export function getEvent(eventId: string): Promise<EventExpectation> {
  return apiGet<EventExpectation>(`/api/v1/events/${encodeURIComponent(eventId)}`);
}

export function getPaperStatus(eventId: string): Promise<PaperStatus> {
  return apiGet<PaperStatus>(`/api/v1/events/${encodeURIComponent(eventId)}/paper-status`);
}

// -- Strategiadraft: draft -> preview -> approve -> persisted --------------

export type StrategyDraftInput = {
  instrument: string;
  event_name: string;
  scheduled_date: string;
  consensus: Record<string, number | string | null>;
  important_kpis: string[];
  bull_case: string[];
  base_case: string[];
  bear_case: string[];
  // Nullable to match the mobile draft editor's lossless text<->value
  // parsing (see textToTypedRecord in strategy-draft-format.ts), which
  // cannot itself prevent a user from typing a null trigger value - the
  // backend's own StrategyDraftPayload.triggers type is the real boundary
  // that rejects one (422), since a trigger conceptually never should be
  // null.
  triggers: Record<string, number | string | null>;
  invalidation_conditions: string[];
  source_name: string | null;
  source_url: string | null;
  source_as_of: string | null;
  change_note: string;
  summary: string;
  assumptions: string[];
  unresolved_questions: string[];
};

export type NormalizedStrategyDraft = StrategyDraftInput & { event_id: string };

export type StrategyDraftPreview = {
  event_id: string;
  base_expectation_version: number;
  draft: NormalizedStrategyDraft;
  draft_fingerprint: string;
  current: EventExpectation;
  changed_fields: string[];
  warnings: string[];
};

// -- Earnings calendar / watchlist (candidate -> tracked, MVP-only) --------
//
// candidate and tracked calendar events never influence the trading worker
// or the PAPER pipeline - see trading_system/calendar_repository.py. This
// is a separate storage boundary from EventExpectation above.

export type CalendarEvent = {
  calendar_event_id: string;
  company_name: string;
  instrument: string;
  market: string;
  event_type: string;
  scheduled_date: string;
  source: string;
  status: 'candidate' | 'tracked' | 'research' | 'decision_to_prepare_strategy' | 'enrich_event_details' | 'preview' | 'approve';
  created_at: string;
  updated_at: string;
};

export function getUpcomingCalendarEvents(fromDate?: string, toDate?: string): Promise<CalendarEvent[]> {
  const params = new URLSearchParams();
  if (fromDate) params.set('from_date', fromDate);
  if (toDate) params.set('to_date', toDate);
  const query = params.toString();
  return apiGet<CalendarEvent[]>(`/api/v1/calendar/upcoming${query ? `?${query}` : ''}`);
}

// Write auth for these two actions deliberately reuses the existing control
// key (never the read key, never a new EXPO_PUBLIC_* secret) - the same
// credential this file already ships for approveStrategyDraft().
export function trackCalendarEvent(calendarEventId: string): Promise<CalendarEvent> {
  return apiPost<CalendarEvent>(
    `/api/v1/calendar/${encodeURIComponent(calendarEventId)}/track`,
    undefined,
    { 'X-MarketAI-Control-Key': CONTROL_API_KEY },
  );
}

export function untrackCalendarEvent(calendarEventId: string): Promise<CalendarEvent> {
  return apiPost<CalendarEvent>(
    `/api/v1/calendar/${encodeURIComponent(calendarEventId)}/untrack`,
    undefined,
    { 'X-MarketAI-Control-Key': CONTROL_API_KEY },
  );
}

// This POST never mutates anything server-side - the backend requires only
// the same read-tier X-MarketAI-Key the rest of this file already uses, not
// the control key. See docs/event_configuration_storage.md.
export function previewStrategyDraft(
  eventId: string,
  draft: StrategyDraftInput,
): Promise<StrategyDraftPreview> {
  return apiPost<StrategyDraftPreview>(
    `/api/v1/events/${encodeURIComponent(eventId)}/strategy-draft/preview`,
    draft,
    { 'X-MarketAI-Key': READ_API_KEY },
  );
}

export type StrategyDraftApprovalRequest = {
  draft: StrategyDraftInput;
  draft_fingerprint: string;
  base_expectation_version: number;
  approved_by: string;
  approved_via?: string;
};

export type StrategyDraftApprovalResult = EventExpectation & {
  draft_fingerprint: string;
  approved_by: string;
};

// The only write in this file. Requires the control key - never the read
// key - and the backend independently re-validates the draft fingerprint
// and expectation-version CAS before persisting anything.
export function approveStrategyDraft(
  eventId: string,
  request: StrategyDraftApprovalRequest,
): Promise<StrategyDraftApprovalResult> {
  return apiPost<StrategyDraftApprovalResult>(
    `/api/v1/events/${encodeURIComponent(eventId)}/strategy-draft/approve`,
    request,
    { 'X-MarketAI-Control-Key': CONTROL_API_KEY },
  );
}
