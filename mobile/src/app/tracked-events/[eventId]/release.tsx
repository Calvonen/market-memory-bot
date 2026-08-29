import { useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { BackButton } from '@/components/back-button';
import {
  getTrackedEventReleaseSource,
  type TrackedEventReleaseSource,
} from '@/services/tracked-events';

export default function TrackedEventReleaseHandoffScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const [releaseSource, setReleaseSource] = useState<TrackedEventReleaseSource | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setReleaseSource(null);
    setError(null);

    async function loadReleaseSource() {
      if (!eventId) {
        setError('Tracked event puuttuu.');
        setLoading(false);
        return;
      }

      try {
        const source = await getTrackedEventReleaseSource(eventId);
        if (!cancelled) {
          setReleaseSource(source);
          setError(null);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Julkaisulähteen lataus epäonnistui.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadReleaseSource();
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <BackButton />
      <Text style={styles.eyebrow}>WORKFLOW · JULKAISU</Text>
      <Text style={styles.title}>Julkaisun tarkistus</Text>
      <Text style={styles.body}>
        Tämä näkymä näyttää canonical tracked-event -workflow’hun liitetyn virallisen
        julkaisulähteen. Lähteen muuttaminen lisätään erillisessä vaiheessa.
      </Text>

      <View style={styles.card}>
        <Text style={styles.label}>Tracked event</Text>
        <Text style={styles.value} selectable>
          {eventId || 'Tuntematon tapahtuma'}
        </Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>Julkaisulähde</Text>
        {loading ? <Text style={styles.value}>Ladataan…</Text> : null}
        {!loading && error ? <Text style={styles.error}>{error}</Text> : null}
        {!loading && !error && releaseSource ? (
          releaseSource.active ? (
            <>
              <Text style={styles.status}>Aktiivinen lähde</Text>
              <Text style={styles.value}>{releaseSource.source_title || 'Nimetön lähde'}</Text>
              <Text style={styles.meta}>{releaseSource.source_kind}</Text>
              <Text style={styles.url} selectable>
                {releaseSource.source_url}
              </Text>
              <Text style={styles.meta}>Versio {releaseSource.version}</Text>
            </>
          ) : (
            <>
              <Text style={styles.status}>Ei aktiivista lähdettä</Text>
              <Text style={styles.meta}>Versio {releaseSource.version}</Text>
            </>
          )
        ) : null}
      </View>

      <Text style={styles.note}>
        Näkymä on edelleen read-only: se ei käynnistä ingestionia, muuta workflow-tilaa eikä luo
        trading taskia.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    backgroundColor: '#0d1118',
    padding: 20,
    paddingTop: 56,
  },
  eyebrow: {
    color: '#8590a1',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
    marginTop: 24,
  },
  title: {
    color: '#f4f7fb',
    fontSize: 24,
    fontWeight: '800',
    marginTop: 6,
  },
  body: {
    color: '#aab3c2',
    fontSize: 14,
    lineHeight: 21,
    marginTop: 12,
  },
  card: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 14,
    padding: 14,
    marginTop: 20,
  },
  label: {
    color: '#8590a1',
    fontSize: 11,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  value: {
    color: '#d8dee8',
    fontSize: 13,
    marginTop: 5,
  },
  status: {
    color: '#f4f7fb',
    fontSize: 14,
    fontWeight: '700',
    marginTop: 8,
  },
  meta: {
    color: '#8994a6',
    fontSize: 12,
    marginTop: 5,
  },
  url: {
    color: '#b9c6d9',
    fontSize: 12,
    lineHeight: 18,
    marginTop: 8,
  },
  error: {
    color: '#f0a0a0',
    fontSize: 13,
    lineHeight: 19,
    marginTop: 8,
  },
  note: {
    color: '#8994a6',
    fontSize: 12,
    lineHeight: 18,
    marginTop: 16,
  },
});
