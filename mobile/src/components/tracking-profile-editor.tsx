import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';

import { shared } from '@/components/screen-shell';
import {
  getTrackingProfiles,
  setTrackingProfile,
  TrackedInstrumentProfile,
  TrackingProfileType,
} from '@/services/tracking-profiles';

const PROFILE_ACTOR = 'mobile-tracking-profile';
const DEFAULT_PROFILE_TYPE: TrackingProfileType = 'trend';
const PROFILE_TYPES: { type: TrackingProfileType; label: string }[] = [
  { type: 'earnings', label: 'Tulosjulkaisut' },
  { type: 'trend', label: 'Trendi' },
  { type: 'future_tech', label: 'Future Tech' },
];

type Props = {
  trackedInstrumentId: string;
};

export function TrackingProfileEditor({ trackedInstrumentId }: Props) {
  return (
    <TrackingProfileEditorGeneration
      key={trackedInstrumentId}
      trackedInstrumentId={trackedInstrumentId}
    />
  );
}

function TrackingProfileEditorGeneration({ trackedInstrumentId }: Props) {
  const [profiles, setProfiles] = useState<TrackedInstrumentProfile[]>([]);
  const [selectedType, setSelectedType] = useState<TrackingProfileType>(DEFAULT_PROFILE_TYPE);
  const [specs, setSpecs] = useState('');
  const [enabled, setEnabled] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const mountedRef = useRef(false);

  useEffect(() => {
    let active = true;
    mountedRef.current = true;

    void getTrackingProfiles(trackedInstrumentId)
      .then((loadedProfiles) => {
        if (!active || !mountedRef.current) return;
        setProfiles(loadedProfiles);
        const current = loadedProfiles.find(
          (profile) => profile.profile_type === DEFAULT_PROFILE_TYPE,
        );
        setSpecs(current?.specs ?? '');
        setEnabled(current?.enabled ?? false);
        setLoaded(true);
        setError('');
      })
      .catch((loadError) => {
        if (!active || !mountedRef.current) return;
        setError(loadError instanceof Error ? loadError.message : 'Profiilien lataus epäonnistui');
      })
      .finally(() => {
        if (active && mountedRef.current) setLoading(false);
      });

    return () => {
      active = false;
      mountedRef.current = false;
    };
  }, [trackedInstrumentId]);

  const canonicalStateReady = loaded;

  function selectProfile(type: TrackingProfileType) {
    if (saving || !canonicalStateReady) return;
    const current = profiles.find((profile) => profile.profile_type === type);
    setSelectedType(type);
    setSpecs(current?.specs ?? '');
    setEnabled(current?.enabled ?? false);
    setError('');
  }

  async function saveProfile() {
    if (!canonicalStateReady || saving) return;

    const saveProfileType = selectedType;
    setSaving(true);
    setError('');
    try {
      const saved = await setTrackingProfile(
        trackedInstrumentId,
        saveProfileType,
        { specs, enabled },
        PROFILE_ACTOR,
      );
      if (!mountedRef.current) return;

      setProfiles((current) => [
        ...current.filter((profile) => profile.profile_type !== saved.profile_type),
        saved,
      ]);
      setSpecs(saved.specs);
      setEnabled(saved.enabled);
    } catch (saveError) {
      if (!mountedRef.current) return;
      setError(saveError instanceof Error ? saveError.message : 'Profiilin tallennus epäonnistui');
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  }

  if (loading && !canonicalStateReady) {
    return <ActivityIndicator />;
  }

  return (
    <View style={{ gap: 8, marginTop: 8 }}>
      <Text style={shared.heading}>Seurantaprofiilit</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {PROFILE_TYPES.map((profile) => {
          const persisted = profiles.find((item) => item.profile_type === profile.type);
          const activeProfile = persisted?.enabled ?? false;
          return (
            <Pressable
              key={profile.type}
              accessibilityRole="button"
              disabled={saving || !canonicalStateReady}
              onPress={() => selectProfile(profile.type)}
              style={[
                shared.card,
                { marginTop: 0, paddingVertical: 8, paddingHorizontal: 10 },
                selectedType === profile.type ? { borderWidth: 2 } : null,
              ]}>
              <Text style={shared.text}>
                {activeProfile ? '✓ ' : ''}{profile.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <Pressable
        accessibilityRole="button"
        disabled={saving || !canonicalStateReady}
        onPress={() => setEnabled((current) => !current)}
        style={shared.button}>
        <Text style={shared.buttonText}>
          {enabled ? 'Poista profiili käytöstä' : 'Ota profiili käyttöön'}
        </Text>
      </Pressable>

      <TextInput
        multiline
        value={specs}
        onChangeText={setSpecs}
        editable={!saving && canonicalStateReady}
        placeholder="Mitä tästä yhtiöstä halutaan seurata?"
        style={[
          shared.card,
          shared.text,
          {
            minHeight: 88,
            textAlignVertical: 'top',
          },
        ]}
      />

      {error ? <Text style={{ color: '#ff8d8d' }}>{error}</Text> : null}

      <Pressable
        accessibilityRole="button"
        disabled={saving || !canonicalStateReady}
        onPress={() => void saveProfile()}
        style={shared.button}>
        <Text style={shared.buttonText}>{saving ? 'Tallennetaan…' : 'Tallenna profiili'}</Text>
      </Pressable>
    </View>
  );
}
