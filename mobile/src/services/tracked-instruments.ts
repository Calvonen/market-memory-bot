import { apiControlPost } from '@/services/api';

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

type TrackedInstrumentPost = <T>(
  path: string,
  body: unknown,
  headers?: Record<string, string>,
) => Promise<T>;

export function trackInstrument(
  input: TrackInstrumentInput,
  actor: string,
  post: TrackedInstrumentPost = apiControlPost,
): Promise<TrackedInstrument> {
  const normalizedActor = actor.trim();
  if (!normalizedActor || normalizedActor.length > 200) {
    return Promise.reject(
      new Error('Tracking actor must be nonblank and at most 200 characters'),
    );
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
