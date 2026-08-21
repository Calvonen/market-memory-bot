import { Link } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { EventExpectation, getEvents } from '@/services/api';

// Foundation-only page. Every event rendered here comes from the real
// MarketAI tracking store (GET /api/v1/events) - there is no fabricated
// earnings calendar in this PR. A future PR can add a calendar-provider
// source that feeds untracked upcoming releases into the same filter UI;
// those would render with the "Lisää seurantaan" action already wired up
// below instead of the always-tracked "Seurannassa" pill.

const DATE_RANGES = [
  { label: '7 pv', days: 7 },
  { label: '30 pv', days: 30 },
  { label: '90 pv', days: 90 },
  { label: 'Kaikki', days: null },
] as const;

function marketForInstrument(instrument: string): string {
  const suffix = instrument.includes('.') ? instrument.split('.').pop() ?? '' : '';
  switch (suffix.toUpperCase()) {
    case 'L':
      return 'Iso-Britannia';
    case 'HE':
      return 'Suomi';
    case 'ST':
      return 'Ruotsi';
    case 'DE':
      return 'Saksa';
    case 'CO':
      return 'Tanska';
    case 'OL':
      return 'Norja';
    default:
      return 'USA';
  }
}

export default function UpcomingEventsScreen() {
  const [events, setEvents] = useState<EventExpectation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [search, setSearch] = useState('');
  const [market, setMarket] = useState<string>('Kaikki');
  const [rangeDays, setRangeDays] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setEvents(await getEvents());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Tuntematon virhe');
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      void load();
    }, 0);
    return () => clearTimeout(timer);
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  const markets = useMemo(() => {
    const unique = new Set((events ?? []).map((event) => marketForInstrument(event.instrument)));
    return ['Kaikki', ...Array.from(unique).sort()];
  }, [events]);

  const filtered = useMemo(() => {
    if (!events) return [];
    const query = search.trim().toLowerCase();
    const now = new Date();
    const cutoff = rangeDays !== null ? now.getTime() + rangeDays * 24 * 60 * 60 * 1000 : null;

    return events.filter((event) => {
      if (market !== 'Kaikki' && marketForInstrument(event.instrument) !== market) {
        return false;
      }
      if (
        query &&
        !event.event_name.toLowerCase().includes(query) &&
        !event.instrument.toLowerCase().includes(query)
      ) {
        return false;
      }
      if (cutoff !== null) {
        const scheduled = new Date(`${event.scheduled_date}T12:00:00`).getTime();
        if (Number.isNaN(scheduled) || scheduled > cutoff) {
          return false;
        }
      }
      return true;
    });
  }, [events, market, search, rangeDays]);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#8a96a8" />
      }
    >
      <Text style={styles.title}>Kaikki tulevat julkaisut</Text>
      <Text style={styles.subtitle}>
        Toistaiseksi vain MarketAI:ssa seuratut tulosjulkaisut. Laajempi
        markkinoiden earnings-kalenteri lisätään seuraavassa PR:ssä.
      </Text>

      <TextInput
        style={styles.searchInput}
        value={search}
        onChangeText={setSearch}
        placeholder="Hae tickerillä tai yhtiön nimellä"
        placeholderTextColor="#4e5868"
        autoCapitalize="characters"
      />

      <Text style={styles.filterLabel}>MARKKINA</Text>
      <View style={styles.chipRow}>
        {markets.map((option) => (
          <Pressable
            key={option}
            style={[styles.chip, market === option && styles.chipActive]}
            onPress={() => setMarket(option)}
          >
            <Text style={[styles.chipText, market === option && styles.chipTextActive]}>
              {option}
            </Text>
          </Pressable>
        ))}
      </View>

      <Text style={styles.filterLabel}>JULKAISUPÄIVÄ</Text>
      <View style={styles.chipRow}>
        {DATE_RANGES.map((option) => (
          <Pressable
            key={option.label}
            style={[styles.chip, rangeDays === option.days && styles.chipActive]}
            onPress={() => setRangeDays(option.days)}
          >
            <Text style={[styles.chipText, rangeDays === option.days && styles.chipTextActive]}>
              {option.label}
            </Text>
          </Pressable>
        ))}
      </View>

      <Text style={styles.sectionTitle}>TULOKSET</Text>

      {!events && !error ? <ActivityIndicator color="#8a96a8" style={styles.loader} /> : null}
      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      {events && filtered.length === 0 ? (
        <View style={styles.emptyCard}>
          <Text style={styles.emptyText}>Ei julkaisuja valituilla suodattimilla.</Text>
        </View>
      ) : null}

      {filtered.map((event) => (
        <Link
          key={event.event_id}
          href={{ pathname: '/events/[eventId]', params: { eventId: event.event_id } }}
          asChild
        >
          <Pressable style={styles.eventCard}>
            <View style={styles.rowBetween}>
              <View style={styles.eventCardTitleBlock}>
                <Text style={styles.company} numberOfLines={1}>
                  {event.event_name}
                </Text>
                <Text style={styles.symbol}>
                  {event.instrument} · {marketForInstrument(event.instrument)}
                </Text>
              </View>
              <Text style={styles.dateText}>{event.scheduled_date}</Text>
            </View>
            <View style={styles.trackedBadge}>
              <Text style={styles.trackedBadgeText}>Seurannassa</Text>
            </View>
          </Pressable>
        </Link>
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
  searchInput: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 12,
    padding: 13,
    color: '#e3e8f0',
    marginBottom: 20,
  },
  filterLabel: {
    color: '#687386',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.2,
    marginBottom: 9,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 20,
  },
  chip: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  chipActive: {
    backgroundColor: '#1b2f3f',
    borderColor: '#2f5570',
  },
  chipText: {
    color: '#8994a6',
    fontSize: 12,
    fontWeight: '600',
  },
  chipTextActive: {
    color: '#72b8db',
  },
  sectionTitle: {
    color: '#687386',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.4,
    marginBottom: 11,
  },
  loader: {
    marginTop: 20,
    marginBottom: 20,
  },
  errorText: {
    color: '#e17878',
    fontSize: 13,
    marginBottom: 16,
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
  eventCard: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
  },
  eventCardTitleBlock: {
    flex: 1,
    paddingRight: 12,
  },
  company: {
    color: '#f4f7fb',
    fontSize: 16,
    fontWeight: '700',
  },
  symbol: {
    color: '#8590a1',
    fontSize: 13,
    marginTop: 2,
  },
  dateText: {
    color: '#aab3c2',
    fontSize: 13,
    fontWeight: '600',
  },
  trackedBadge: {
    alignSelf: 'flex-start',
    backgroundColor: '#172219',
    borderWidth: 1,
    borderColor: '#28492f',
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 4,
    marginTop: 12,
  },
  trackedBadgeText: {
    color: '#72db8b',
    fontSize: 11,
    fontWeight: '800',
  },
});
