import { useFocusEffect } from 'expo-router';
import { useCallback, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import {
  getTrackedInstruments,
  TrackedInstrument,
} from '@/services/tracked-instruments';

type Props = {
  refreshToken?: number;
};

export function HomeTrackedCompaniesSection({ refreshToken = 0 }: Props) {
  const [instruments, setInstruments] = useState<TrackedInstrument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latestLoadId = useRef(0);

  const load = useCallback(() => {
    const loadId = ++latestLoadId.current;
    setError(null);
    return getTrackedInstruments()
      .then((list) => {
        if (loadId !== latestLoadId.current) return;
        setInstruments(list);
      })
      .catch((err) => {
        if (loadId !== latestLoadId.current) return;
        setError(err instanceof Error ? err.message : 'Seurattuja yhtiöitä ei juuri nyt saatu haettua.');
      });
  }, []);

  useFocusEffect(
    useCallback(() => {
      const timer = setTimeout(() => {
        void load();
      }, 0);
      return () => clearTimeout(timer);
    }, [load, refreshToken]),
  );

  const activeInstruments = useMemo(
    () =>
      (instruments ?? [])
        .filter((instrument) => instrument.active)
        .sort((a, b) => a.instrument.localeCompare(b.instrument)),
    [instruments],
  );

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

      {instruments && activeInstruments.length === 0 && !error ? (
        <View style={styles.emptyCard}>
          <Text style={styles.emptyText}>Ei vielä seurattuja yhtiöitä.</Text>
        </View>
      ) : null}

      {activeInstruments.map((instrument) => (
        <View key={instrument.id} style={styles.companyCard}>
          <View style={styles.titleBlock}>
            <Text style={styles.company} numberOfLines={1}>
              {instrument.company_name || instrument.instrument}
            </Text>
            <Text style={styles.symbol}>{instrument.instrument}</Text>
          </View>
          {instrument.market ? <Text style={styles.market}>{instrument.market}</Text> : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  section: {
    marginBottom: 24,
  },
  sectionTitle: {
    color: '#687386',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.4,
    marginBottom: 11,
    marginLeft: 3,
  },
  loader: {
    marginVertical: 16,
  },
  companyCard: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 16,
    padding: 16,
    marginBottom: 10,
  },
  titleBlock: {
    flex: 1,
    paddingRight: 12,
  },
  company: {
    color: '#f4f7fb',
    fontSize: 17,
    fontWeight: '700',
  },
  symbol: {
    color: '#8590a1',
    fontSize: 13,
    marginTop: 2,
  },
  market: {
    color: '#aab3c2',
    fontSize: 12,
    fontWeight: '600',
  },
  emptyCard: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 16,
    padding: 18,
  },
  emptyText: {
    color: '#8994a6',
    fontSize: 14,
  },
  errorCard: {
    backgroundColor: '#1c1417',
    borderWidth: 1,
    borderColor: '#3a2226',
    borderRadius: 16,
    padding: 16,
  },
  errorText: {
    color: '#e17878',
    fontSize: 13,
  },
  retryButton: {
    alignSelf: 'flex-start',
    backgroundColor: '#2a1b1e',
    borderWidth: 1,
    borderColor: '#4a2b30',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginTop: 12,
  },
  retryButtonText: {
    color: '#e17878',
    fontSize: 12,
    fontWeight: '800',
  },
});
