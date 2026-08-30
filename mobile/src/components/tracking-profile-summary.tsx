import { Text } from 'react-native';

import { shared } from '@/components/screen-shell';
import {
  TrackedInstrumentProfile,
  TrackingProfileType,
} from '@/services/tracking-profiles';

const PROFILE_LABELS: Record<TrackingProfileType, string> = {
  earnings: 'Tulosjulkaisut',
  trend: 'Trendi',
  future_tech: 'Future Tech',
};

const PROFILE_ORDER: TrackingProfileType[] = ['earnings', 'trend', 'future_tech'];

type Props = {
  profiles: TrackedInstrumentProfile[];
};

export function TrackingProfileSummary({ profiles }: Props) {
  const enabledTypes = new Set(
    profiles.filter((profile) => profile.enabled).map((profile) => profile.profile_type),
  );
  const labels = PROFILE_ORDER.filter((type) => enabledTypes.has(type)).map(
    (type) => PROFILE_LABELS[type],
  );

  if (labels.length === 0) return null;

  return <Text style={shared.text}>{labels.join(' · ')}</Text>;
}
