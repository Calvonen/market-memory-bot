import { useFocusEffect } from 'expo-router';
import { useCallback, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, View } from 'react-native';

import { TrackingProfileEditor } from '@/components/tracking-profile-editor';
import {
  deactivateTrackedInstrument,
  getTrackedInstruments,
  TrackedInstrument,
} from '@/services/tracked-instruments';

const MANAGEMENT_ACTOR = 'mobile-home-tracking-management';

type Props = { refreshToken?: number };

export function HomeTrackedCompaniesSection({ refreshToken = 0 }: Props) {
  const [instruments, setInstruments] = useState<TrackedInstrument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [managementError, setManagementError] = useState<string | null>(null);
  const latestLoadId = useRef(0);

  const load = useCallback(() => {
    const loadId = ++latestLoadId.current;
    setError(null);
    setInstruments(null);
    return getTrackedInstruments()
      .then((list) => {
        if (loadId !== latestLoadId.current) return;
        setInstruments(list);
      })
      .catch((err) => {
        if (loadId !== latestLoadId.current) return;
        setInstruments(null);
        setExpandedId(null);
        setError(err instanceof Error ? err.message : 'Seurattuja yhtiöitä ei juuri nyt saatu haettua.');
      });
  }, []);

  useFocusEffect(
    useCallback(() => {
      const timer = setTimeout(() => void load(), 0);
      return () => clearTimeout(timer);
    }, [load, refreshToken]),
  );

  const activeInstruments = useMemo(
    () => (instruments ?? []).filter((item) => item.active).sort((a, b) => a.instrument.localeCompare(b.instrument)),
    [instruments],
  );

  async function remove(instrument: TrackedInstrument) {
    if (removingId) return;
    setRemovingId(instrument.id);
    setManagementError(null);
    try {
      const saved = await deactivateTrackedInstrument(instrument.id, MANAGEMENT_ACTOR);
      if (saved.active) throw new Error('Seurannan poistaminen ei vahvistunut palvelimelta.');
      setInstruments((current) => (current ?? []).map((item) => item.id === saved.id ? saved : item));
      setExpandedId(null);
    } catch (err) {
      setManagementError(err instanceof Error ? err.message : 'Seurannasta poistaminen epäonnistui.');
    } finally {
      setRemovingId(null);
    }
  }

  function confirmRemove(instrument: TrackedInstrument) {
    Alert.alert(
      'Poista seurannasta?',
      `${instrument.company_name || instrument.instrument} poistetaan aktiivisesta seurannasta. Historia säilyy.`,
      [
        { text: 'Peruuta', style: 'cancel' },
        { text: 'Poista seurannasta', style: 'destructive', onPress: () => void remove(instrument) },
      ],
    );
  }

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>SEURATUT YHTIÖT</Text>
      {!instruments && !error ? <ActivityIndicator color="#8a96a8" style={styles.loader} /> : null}
      {error ? (
        <View style={styles.errorCard}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.retryButton} onPress={() => void load()}>
            <Text style={styles.retryButtonText}>Yritä uudelleen</Text>
          </Pressable>
        </View>
      ) : null}
      {managementError ? <Text style={styles.errorText}>{managementError}</Text> : null}
      {instruments && activeInstruments.length === 0 && !error ? (
        <View style={styles.emptyCard}><Text style={styles.emptyText}>Ei vielä seurattuja yhtiöitä.</Text></View>
      ) : null}
      {activeInstruments.map((instrument) => {
        const expanded = expandedId === instrument.id;
        return (
          <View key={instrument.id} style={styles.companyCard}>
            <Pressable style={styles.companyHeader} onPress={() => setExpandedId(expanded ? null : instrument.id)}>
              <View style={styles.titleBlock}>
                <Text style={styles.company} numberOfLines={1}>{instrument.company_name || instrument.instrument}</Text>
                <Text style={styles.symbol}>{instrument.instrument}</Text>
              </View>
              <View style={styles.rightBlock}>
                {instrument.market ? <Text style={styles.market}>{instrument.market}</Text> : null}
                <Text style={styles.manageText}>{expanded ? 'Sulje' : 'Hallitse'}</Text>
              </View>
            </Pressable>
            {expanded ? (
              <View style={styles.managementBlock}>
                <TrackingProfileEditor trackedInstrumentId={instrument.id} />
                <Pressable disabled={removingId === instrument.id} onPress={() => confirmRemove(instrument)} style={styles.removeButton}>
                  <Text style={styles.removeButtonText}>{removingId === instrument.id ? 'Poistetaan…' : 'Poista seurannasta'}</Text>
                </Pressable>
              </View>
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { marginBottom: 24 },
  sectionTitle: { color: '#687386', fontSize: 11, fontWeight: '800', letterSpacing: 1.4, marginBottom: 11, marginLeft: 3 },
  loader: { marginVertical: 16 },
  companyCard: { backgroundColor: '#131821', borderWidth: 1, borderColor: '#202734', borderRadius: 16, padding: 16, marginBottom: 10 },
  companyHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  titleBlock: { flex: 1, paddingRight: 12 },
  rightBlock: { alignItems: 'flex-end', gap: 5 },
  company: { color: '#f4f7fb', fontSize: 17, fontWeight: '700' },
  symbol: { color: '#8590a1', fontSize: 13, marginTop: 2 },
  market: { color: '#aab3c2', fontSize: 12, fontWeight: '600' },
  manageText: { color: '#72b8db', fontSize: 12, fontWeight: '700' },
  managementBlock: { borderTopWidth: 1, borderTopColor: '#202734', marginTop: 14, paddingTop: 10 },
  removeButton: { borderWidth: 1, borderColor: '#5a2b31', backgroundColor: '#241519', borderRadius: 12, paddingVertical: 11, paddingHorizontal: 14, marginTop: 14, alignItems: 'center' },
  removeButtonText: { color: '#ef8c96', fontSize: 13, fontWeight: '800' },
  emptyCard: { backgroundColor: '#131821', borderWidth: 1, borderColor: '#202734', borderRadius: 16, padding: 18 },
  emptyText: { color: '#8994a6', fontSize: 14 },
  errorCard: { backgroundColor: '#1c1417', borderWidth: 1, borderColor: '#3a2226', borderRadius: 16, padding: 16 },
  errorText: { color: '#e17878', fontSize: 13, marginBottom: 10 },
  retryButton: { alignSelf: 'flex-start', backgroundColor: '#2a1b1e', borderWidth: 1, borderColor: '#4a2b30', borderRadius: 12, paddingHorizontal: 14, paddingVertical: 8, marginTop: 12 },
  retryButtonText: { color: '#e17878', fontSize: 12, fontWeight: '800' },
});
