import { Link } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

import { BackButton } from '@/components/back-button';
import {
  CalendarEvent,
  EventExpectation,
  getEvents,
  getUpcomingCalendarEvents,
  trackCalendarEvent,
} from '@/services/api';

// Merges the real MarketAI tracking store (GET /api/v1/events) with the
// earnings-calendar candidate/watchlist store (GET /api/v1/calendar/upcoming).
// A calendar candidate/tracked row never influences trading by itself - see
// trading_system/calendar_repository.py - it only ever adds "things worth
// noticing", with an explicit "Lisää seurantaan" action per card.

const DATE_RANGES = [
  { label: '7 pv', days: 7 },
  { label: '30 pv', days: 30 },
  { label: '90 pv', days: 90 },
  { label: 'Kaikki', days: null },
] as const;

function marketForInstrument(instrument: string): string {
  // No suffix is the actual USA convention on this backend (e.g. "AAPL").
  // An unrecognized suffix (".PA", ".AS", ".SW", ...) must not be guessed
  // as USA - it gets its own bucket instead of a wrong market label.
  if (!instrument.includes('.')) {
    return 'USA';
  }
  const suffix = instrument.split('.').pop() ?? '';
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
      return `Muu (.${suffix.toUpperCase()})`;
  }
}

export type UpcomingRow = {
  key: string;
  companyName: string;
  instrument: string;
  market: string;
  eventType: string;
  scheduledDate: string;
  // 'expectation' rows are always already tracked via the real trading
  // system (EventExpectation) - they have no candidate/tracked action of
  // their own here. 'calendar' rows carry the candidate/tracked lifecycle
  // status from the calendar/watchlist store.
  kind: 'expectation' | 'calendar';
  status: 'candidate' | 'tracked';
  eventId: string | null;
  calendarEventId: string | null;
};

export function mergeUpcomingRows(
  events: EventExpectation[],
  calendarEvents: CalendarEvent[],
): UpcomingRow[] {
  const expectationRows: UpcomingRow[] = events.map((event) => ({
    key: `expectation:${event.event_id}`,
    companyName: event.event_name,
    instrument: event.instrument,
    market: marketForInstrument(event.instrument),
    eventType: 'earnings',
    scheduledDate: event.scheduled_date,
    kind: 'expectation',
    status: 'tracked',
    eventId: event.event_id,
    calendarEventId: null,
  }));

  // Hays and any other *occurrence* already tracked through the real
  // trading system must never also show up as an untracked calendar
  // candidate - the calendar provider (e.g. Finnhub) has no idea that
  // occurrence is already tracked elsewhere, so this app-side dedupe is
  // what prevents the duplicate card. Deliberately keyed on instrument +
  // scheduled_date, not instrument alone: /api/v1/events intentionally
  // keeps historical expectations forever (see upcoming.tsx's own
  // date-range filter comment above), so an instrument-only match would
  // let a past AAPL release hide AAPL's next, genuinely different,
  // quarterly candidate indefinitely. EventExpectation has no explicit
  // event_type of its own (it only ever represents "earnings"), so the
  // match only applies to calendar rows that are themselves 'earnings' -
  // a non-earnings candidate (e.g. a manual production report) for the
  // same instrument+date is a different occurrence and must never be
  // dropped by this.
  const trackedEarningsOccurrences = new Set(
    events.map((event) => `${event.instrument.toUpperCase()}|${event.scheduled_date}`),
  );

  const calendarRows: UpcomingRow[] = calendarEvents
    .filter((event) => {
      if (event.event_type !== 'earnings') return true;
      const key = `${event.instrument.toUpperCase()}|${event.scheduled_date}`;
      return !trackedEarningsOccurrences.has(key);
    })
    .map((event) => ({
      key: `calendar:${event.calendar_event_id}`,
      companyName: event.company_name,
      instrument: event.instrument,
      market: event.market,
      eventType: event.event_type,
      scheduledDate: event.scheduled_date,
      kind: 'calendar',
      status: event.status === 'tracked' ? 'tracked' : 'candidate',
      eventId: null,
      calendarEventId: event.calendar_event_id,
    }));

  // Deterministic order: upcoming (today or later) first, soonest first;
  // history after, most recently released first - mirroring the backend's
  // own list_upcoming() ordering
  // (SupabaseEventExpectationRepository._list_upcoming_sort_key). Sorted
  // here, on the combined array, not left as "expectations then calendar
  // rows" concatenation order: /api/v1/events deliberately returns every
  // historical expectation, so without this sort every upcoming calendar
  // candidate would render below the *entire* accumulated history.
  // Candidate/tracked origin never decides order - only scheduled_date
  // does.
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();

  return [...expectationRows, ...calendarRows].sort((a, b) => {
    const aTime = new Date(`${a.scheduledDate}T12:00:00`).getTime();
    const bTime = new Date(`${b.scheduledDate}T12:00:00`).getTime();
    const aPast = Number.isNaN(aTime) || aTime < startOfToday;
    const bPast = Number.isNaN(bTime) || bTime < startOfToday;
    if (aPast !== bPast) return aPast ? 1 : -1;
    return aPast ? bTime - aTime : aTime - bTime;
  });
}

export default function UpcomingEventsScreen() {
  const [events, setEvents] = useState<EventExpectation[] | null>(null);
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [trackingIds, setTrackingIds] = useState<Set<string>>(new Set());
  const [trackError, setTrackError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  const [market, setMarket] = useState<string>('Kaikki');
  const [rangeDays, setRangeDays] = useState<number | null>(null);
  // Separate generation counters for the two independent sources - never a
  // single shared one. A track mutation only ever needs to invalidate a
  // stale in-flight *calendar* response (see onTrack() below); it must
  // never also invalidate an unrelated, still-pending getEvents() response,
  // or that response would be discarded on arrival and leave `events`
  // stuck null until another refresh.
  const latestEventsLoadId = useRef(0);
  const latestCalendarLoadId = useRef(0);

  const load = useCallback(() => {
    // Guard against overlapping load() calls (e.g. pull-to-refresh fired
    // again while the first requests are still pending): an older response
    // resolving after a newer one must never replace the already refreshed
    // lists, or set an obsolete error over a successful refresh. Both
    // generations are bumped together here, so two overlapping load()
    // calls still invalidate each other's responses on both sources, same
    // as before this split.
    const eventsLoadId = ++latestEventsLoadId.current;
    const calendarLoadId = ++latestCalendarLoadId.current;
    setError(null);

    // Both requests are started here, before either is awaited - a
    // slow/never-settling getEvents() must never delay
    // getUpcomingCalendarEvents() from even starting, and vice versa.
    // Each one's .then/.catch below is its own independent chain (not a
    // sequential await), so its state update fires the moment *that*
    // request settles, regardless of whether the other one has settled,
    // failed, or is still pending - a stalled getEvents() can never hold
    // the calendar list (or the loading state, which clears as soon as
    // either source has data/an error) hostage, and vice versa.
    const eventsPromise = getEvents()
      .then((list) => {
        if (eventsLoadId !== latestEventsLoadId.current) return;
        setEvents(list);
      })
      .catch((err) => {
        if (eventsLoadId !== latestEventsLoadId.current) return;
        setError(err instanceof Error ? err.message : 'Tuntematon virhe');
      });

    const calendarPromise = getUpcomingCalendarEvents()
      .then((list) => {
        if (calendarLoadId !== latestCalendarLoadId.current) return;
        setCalendarEvents(list);
      })
      .catch(() => {
        if (calendarLoadId !== latestCalendarLoadId.current) return;
        setCalendarEvents([]);
      });

    // onRefresh() below awaits load() to know when to stop spinning - it
    // uses the exact same "first source to settle wins" policy the
    // `loading` flag above already uses (loading clears the moment either
    // source has data/an error, not once both do), so refresh and initial
    // load never behave differently. Promise.race, not Promise.all: if one
    // request hangs forever, the refresh indicator must still clear as
    // soon as the *other* one settles, rather than waiting indefinitely on
    // the stalled one - the hung source's own .then()/.catch() above still
    // updates its state normally whenever (if ever) it does settle.
    return Promise.race([eventsPromise, calendarPromise]);
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

  const onTrack = useCallback(async (calendarEventId: string) => {
    setTrackError(null);
    setTrackingIds((prev) => new Set(prev).add(calendarEventId));
    try {
      const updated = await trackCalendarEvent(calendarEventId);
      setCalendarEvents((prev) =>
        (prev ?? []).map((event) => (event.calendar_event_id === calendarEventId ? updated : event)),
      );
      // Invalidates only a calendar response still in flight (e.g. a
      // pull-to-refresh GET started before this track request resolved):
      // that GET's captured calendarLoadId can never match
      // latestCalendarLoadId.current again, so its eventual
      // setCalendarEvents(list) - built from a snapshot taken before this
      // track mutation happened - is rejected by the same staleness guard
      // load() already uses, instead of silently reverting this row back
      // to 'candidate'. Deliberately never bumps latestEventsLoadId - an
      // independent, still-pending getEvents() response for the unrelated
      // expectation list must still apply normally when it arrives. A
      // load() started *after* this point captures a fresh
      // calendarLoadId from the bumped counter and is unaffected.
      ++latestCalendarLoadId.current;
    } catch (err) {
      setTrackError(err instanceof Error ? err.message : 'Seurannan aloitus epäonnistui');
    } finally {
      setTrackingIds((prev) => {
        const next = new Set(prev);
        next.delete(calendarEventId);
        return next;
      });
    }
  }, []);

  const rows = useMemo(
    () => mergeUpcomingRows(events ?? [], calendarEvents ?? []),
    [events, calendarEvents],
  );

  const markets = useMemo(() => {
    const unique = new Set(rows.map((row) => row.market));
    return ['Kaikki', ...Array.from(unique).sort()];
  }, [rows]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    const now = new Date();
    // Start-of-today, so a day-range filter means "the next N days" and
    // excludes already-released events - "Kaikki" (rangeDays === null) is
    // unaffected and still shows the full tracked history.
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const cutoff = rangeDays !== null ? startOfToday + rangeDays * 24 * 60 * 60 * 1000 : null;

    return rows.filter((row) => {
      if (market !== 'Kaikki' && row.market !== market) {
        return false;
      }
      if (
        query &&
        !row.companyName.toLowerCase().includes(query) &&
        !row.instrument.toLowerCase().includes(query)
      ) {
        return false;
      }
      if (cutoff !== null) {
        const scheduled = new Date(`${row.scheduledDate}T12:00:00`).getTime();
        if (Number.isNaN(scheduled) || scheduled < startOfToday || scheduled > cutoff) {
          return false;
        }
      }
      return true;
    });
  }, [rows, market, search, rangeDays]);

  const loading = !events && !calendarEvents && !error;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#8a96a8" />
      }
    >
      <BackButton label="Etusivulle" />

      <Text style={styles.title}>Kaikki tulevat julkaisut</Text>
      <Text style={styles.subtitle}>
        Seuratut MarketAI-eventit ja tulossa olevat tulosjulkaisut samassa
        listassa. Lisää kiinnostava yhtiö seurantaan alta.
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

      {loading ? <ActivityIndicator color="#8a96a8" style={styles.loader} /> : null}
      {error ? <Text style={styles.errorText}>{error}</Text> : null}
      {trackError ? <Text style={styles.errorText}>{trackError}</Text> : null}

      {!loading && filtered.length === 0 ? (
        <View style={styles.emptyCard}>
          <Text style={styles.emptyText}>Ei julkaisuja valituilla suodattimilla.</Text>
        </View>
      ) : null}

      {filtered.map((row) => {
        const cardBody = (
          <>
            <View style={styles.rowBetween}>
              <View style={styles.eventCardTitleBlock}>
                <Text style={styles.company} numberOfLines={1}>
                  {row.companyName}
                </Text>
                <Text style={styles.symbol}>
                  {row.instrument} · {row.market}
                </Text>
              </View>
              <Text style={styles.dateText}>{row.scheduledDate}</Text>
            </View>
            {row.status === 'tracked' ? (
              <View style={styles.trackedBadge}>
                <Text style={styles.trackedBadgeText}>Seurannassa</Text>
              </View>
            ) : (
              <Pressable
                style={styles.trackButton}
                disabled={trackingIds.has(row.calendarEventId ?? '')}
                onPress={() => row.calendarEventId && void onTrack(row.calendarEventId)}
              >
                <Text style={styles.trackButtonText}>
                  {trackingIds.has(row.calendarEventId ?? '') ? 'Lisätään…' : 'Lisää seurantaan'}
                </Text>
              </Pressable>
            )}
          </>
        );

        if (row.kind === 'expectation' && row.eventId) {
          return (
            <Link
              key={row.key}
              href={{ pathname: '/events/[eventId]', params: { eventId: row.eventId } }}
              asChild
            >
              <Pressable style={styles.eventCard}>{cardBody}</Pressable>
            </Link>
          );
        }

        return (
          <View key={row.key} style={styles.eventCard}>
            {cardBody}
          </View>
        );
      })}
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
  trackButton: {
    alignSelf: 'flex-start',
    backgroundColor: '#1b2f3f',
    borderWidth: 1,
    borderColor: '#2f5570',
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginTop: 12,
  },
  trackButtonText: {
    color: '#72b8db',
    fontSize: 12,
    fontWeight: '800',
  },
});
