import { apiControlPost, apiGet } from '@/services/api';

export type TrackedInstrumentSource = 'scanner' | 'calendar' | 'manual';

export type TrackedInstrument = {
  id: string;
  instrument: string;
  market: string;
  company_name: string;
  sources: TrackedInstrumentSource[];
  active: boolean;
  created_by: string;
  updated_by: string;
};

export type TrackInstrumentInput = {
  instrument: string;
  company_name?: string;
  market?: string;
  source: TrackedInstrumentSource;
};

type TrackedInstrumentGet = <T>(path: string) => Promise<T>;
type TrackedInstrumentPost = <T>(
  path: string,
  body: unknown,
  headers?: Record<string, string>,
) => Promise<T>;

function normalizeActor(actor: string): string {
  const normalizedActor = actor.trim();
  if (!normalizedActor || normalizedActor.length > 200) {
    throw new Error('Tracking actor must be nonblank and at most 200 characters');
  }
  return normalizedActor;
}

export function getTrackedInstruments(
  get: TrackedInstrumentGet = apiGet,
): Promise<TrackedInstrument[]> {
  return get<TrackedInstrument[]>('/api/v1/tracked-instruments');
}

export function trackInstrument(
  input: TrackInstrumentInput,
  actor: string,
  post: TrackedInstrumentPost = apiControlPost,
): Promise<TrackedInstrument> {
  let normalizedActor: string;
  try {
    normalizedActor = normalizeActor(actor);
  } catch (error) {
    return Promise.reject(error);
  }

  const normalizedInstrument = input.instrument.trim();
  if (!normalizedInstrument) {
    return Promise.reject(new Error('Instrument must be nonblank'));
  }

  return post<TrackedInstrument>(
    '/api/v1/tracked-instruments',
    {
      instrument: normalizedInstrument,
      company_name: input.company_name?.trim() ?? '',
      market: input.market?.trim() ?? '',
      source: input.source,
    },
    { 'X-MarketAI-Actor': normalizedActor },
  );
}

export function deactivateTrackedInstrument(
  trackedInstrumentId: string,
  actor: string,
  post: TrackedInstrumentPost = apiControlPost,
): Promise<TrackedInstrument> {
  const normalizedId = trackedInstrumentId.trim();
  if (!normalizedId) {
    return Promise.reject(new Error('Tracked instrument id must be nonblank'));
  }

  let normalizedActor: string;
  try {
    normalizedActor = normalizeActor(actor);
  } catch (error) {
    return Promise.reject(error);
  }

  return post<TrackedInstrument>(
    `/api/v1/tracked-instruments/${encodeURIComponent(normalizedId)}/deactivate`,
    {},
    { 'X-MarketAI-Actor': normalizedActor },
  );
}
