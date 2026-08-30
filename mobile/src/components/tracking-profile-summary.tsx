import { useEffect, useState } from 'react';
import { Text } from 'react-native';

import { shared } from '@/components/screen-shell';
import { getTrackingProfiles, TrackingProfileType } from '@/services/tracking-profiles';

const PROFILE_LABELS: Record<TrackingProfileType, string> = {
  earnings: 'Tulosjulkaisut',
  trend: 'Trendi',
  future_tech: 'Future Tech',
};

const PROFILE_ORDER: TrackingProfileType[] = ['earnings', 'trend', 'future_tech'];

type Props = {
  trackedInstrumentId: string;
  refreshToken?: number;
};

export function TrackingProfileSummary({ trackedInstrumentId, refreshToken = 0 }: Props) {
  const [labels, setLabels] = useState<string[]>([]);

  useEffect(() => {
    let active = true;

    void getTrackingProfiles(trackedInstrumentId)
      .then((profiles) => {
        if (!active) return;
        const enabledTypes = new Set(
          profiles.filter((profile) => profile.enabled).map((profile) => profile.profile_type),
        );
        setLabels(
          PROFILE_ORDER.filter((type) => enabledTypes.has(type)).map((type) => PROFILE_LABELS[type]),
        );
      })
      .catch(() => {
        // Profile annotations are supplemental; keep the scanner row usable.
      });

    return () => {
      active = false;
    };
  }, [trackedInstrumentId, refreshToken]);

  if (labels.length === 0) return null;

  return <Text style={shared.text}>{labels.join(' · ')}</Text>;
}
