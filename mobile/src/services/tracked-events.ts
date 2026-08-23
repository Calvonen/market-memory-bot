import { apiGet } from '@/services/api';

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
