import { apiGet, apiPut } from '@/services/api';

export type TrackingProfileType = 'earnings' | 'trend' | 'future_tech';

export type TrackedInstrumentProfile = {
  id: string;
  tracked_instrument_id: string;
  profile_type: TrackingProfileType;
  specs: string;
  enabled: boolean;
  created_by: string;
  updated_by: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type TrackingProfileInput = {
  specs?: string;
  enabled?: boolean;
};

type TrackingProfileGet = <T>(path: string) => Promise<T>;
type TrackingProfilePut = <T>(
  path: string,
  body: unknown,
  headers?: Record<string, string>,
) => Promise<T>;

function codePointLength(value: string): number {
  return Array.from(value).length;
}

function normalizeTrackedInstrumentId(trackedInstrumentId: string): string {
  const normalized = trackedInstrumentId.trim();
  if (!normalized) {
    throw new Error('Tracked instrument id must be nonblank');
  }
  return normalized;
}

export function getTrackingProfiles(
  trackedInstrumentId: string,
  get: TrackingProfileGet = apiGet,
): Promise<TrackedInstrumentProfile[]> {
  const instrumentId = normalizeTrackedInstrumentId(trackedInstrumentId);
  return get<TrackedInstrumentProfile[]>(
    `/api/v1/tracked-instruments/${encodeURIComponent(instrumentId)}/profiles`,
  );
}

export function getTrackingProfilesBatch(
  trackedInstrumentIds: string[],
  get: TrackingProfileGet = apiGet,
): Promise<Record<string, TrackedInstrumentProfile[]>> {
  const instrumentIds = Array.from(
    new Set(trackedInstrumentIds.map(normalizeTrackedInstrumentId)),
  );
  if (instrumentIds.length === 0) {
    return Promise.resolve({});
  }
  if (instrumentIds.length > 50) {
    return Promise.reject(new Error('At most 50 tracked instrument ids are allowed'));
  }

  const query = instrumentIds
    .map((instrumentId) => `tracked_instrument_id=${encodeURIComponent(instrumentId)}`)
    .join('&');
  return get<Record<string, TrackedInstrumentProfile[]>>(
    `/api/v1/tracked-instrument-profiles?${query}`,
  );
}

export function setTrackingProfile(
  trackedInstrumentId: string,
  profileType: TrackingProfileType,
  input: TrackingProfileInput,
  actor: string,
  put: TrackingProfilePut = apiPut,
): Promise<TrackedInstrumentProfile> {
  const instrumentId = normalizeTrackedInstrumentId(trackedInstrumentId);
  const normalizedActor = actor.trim();
  if (!normalizedActor || codePointLength(normalizedActor) > 200) {
    return Promise.reject(
      new Error('Tracking actor must be nonblank and at most 200 characters'),
    );
  }

  const specs = input.specs?.trim() ?? '';
  if (codePointLength(specs) > 4000) {
    return Promise.reject(new Error('Tracking specs must be at most 4000 characters'));
  }

  return put<TrackedInstrumentProfile>(
    `/api/v1/tracked-instruments/${encodeURIComponent(instrumentId)}/profiles/${encodeURIComponent(profileType)}`,
    {
      specs,
      enabled: input.enabled ?? true,
    },
    { 'X-MarketAI-Actor': normalizedActor },
  );
}
