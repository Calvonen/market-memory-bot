import { useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { BackButton } from '@/components/back-button';
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
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
    >
      <BackButton label="Tapahtumat" />

      <Text style={styles.title}>Historia</Text>
      <Text style={styles.subtitle}>Päättyneet seurannat yli 24 tunnin takaa</Text>

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
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: '#0b0e13',
  },
  content: {
    paddingHorizontal: 18,
    paddingTop: 58,
    paddingBottom: 48,
  },
  title: {
    color: '#f4f7fb',
    fontSize: 24,
    fontWeight: '800',
  },
  subtitle: {
    color: '#8590a1',
    fontSize: 13,
    lineHeight: 19,
    marginTop: 6,
    marginBottom: 22,
  },
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
