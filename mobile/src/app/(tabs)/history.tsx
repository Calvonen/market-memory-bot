import { useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ScreenShell } from '@/components/screen-shell';
import { TrackedEventCard } from '@/components/TrackedEventsSection';
import { getTrackedEvents, TrackedMarketEvent } from '@/services/tracked-events';

export default function HistoryScreen() {
  const [events, setEvents] = useState<TrackedMarketEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latestLoadId = useRef(0);

  const load = useCallback(() => {
    const loadId = ++latestLoadId.current;
    setError(null);
    return getTrackedEvents('history')
      .then((list) => {
        if (loadId !== latestLoadId.current) return;
        setEvents(list);
      })
      .catch((err) => {
        if (loadId !== latestLoadId.current) return;
        setError(err instanceof Error ? err.message : 'Historiatietoja ei juuri nyt saatu haettua.');
      });
  }, []);

  useFocusEffect(
    useCallback(() => {
      const timer = setTimeout(() => {
        void load();
      }, 0);
      return () => clearTimeout(timer);
    }, [load]),
  );

  return (
    <ScreenShell title="Historia" subtitle="Päättyneet seurannat yli 24 tunnin takaa">
      {error ? (
        <View style={styles.errorCard}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.retryButton} onPress={() => void load()}>
            <Text style={styles.retryText}>Yritä uudelleen</Text>
          </Pressable>
        </View>
      ) : null}

      {events?.length === 0 && !error ? (
        <View style={styles.emptyCard}>
          <Text style={styles.emptyTitle}>Historia on vielä tyhjä</Text>
          <Text style={styles.emptyText}>
            Valmistuneet, epäonnistuneet ja perutut seurannat siirtyvät tänne 24 tunnin jälkeen.
          </Text>
        </View>
      ) : null}

      {events?.map((event) => (
        <TrackedEventCard key={event.event_id} event={event} />
      ))}
    </ScreenShell>
  );
}

const styles = StyleSheet.create({
  emptyCard: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 16,
    padding: 16,
  },
  emptyTitle: {
    color: '#f4f7fb',
    fontSize: 16,
    fontWeight: '700',
  },
  emptyText: {
    color: '#8994a6',
    fontSize: 13,
    marginTop: 6,
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
  retryText: {
    color: '#e17878',
    fontSize: 12,
    fontWeight: '800',
  },
});
