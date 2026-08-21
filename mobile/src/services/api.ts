export const API_URL = process.env.EXPO_PUBLIC_MARKETAI_API_URL ?? 'http://127.0.0.1:8001';
const READ_API_KEY = process.env.EXPO_PUBLIC_MARKETAI_READ_API_KEY ?? '';

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { headers: { 'X-MarketAI-Key': READ_API_KEY } });
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
