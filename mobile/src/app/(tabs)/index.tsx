import { Link, router } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import {
  CalendarEvent,
  EventExpectation,
  getEvents,
  getPaperStatus,
  getUpcomingCalendarEvents,
  PaperRun,
} from '@/services/api';

type EventStatus = { run: PaperRun | null; statusError: boolean };

// Mirrors MAX_CALENDAR_LOOKAHEAD_DAYS in trading_system/api.py - the widest
// window the backend accepts, and what an omitted from_date/to_date used to
// resolve to server-side. Sending it explicitly (below) keeps that window
// anchored to the device's own local day instead of the backend host's.
const CALENDAR_WINDOW_DAYS = 30;

// Same principle as events/upcoming.tsx's deviceLocalDateWindow(): "today"
// must come from the device's own local calendar day (getFullYear/
// getMonth/getDate), never toISOString() (UTC) - a device whose local day
// has already rolled over relative to UTC would otherwise get a window
// computed against the wrong day.
function formatLocalDate(date: Date): string {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function deviceLocalCalendarWindow(): { fromDate: string; toDate: string } {
  const now = new Date();
  const to = new Date(now.getFullYear(), now.getMonth(), now.getDate() + CALENDAR_WINDOW_DAYS);
  return { fromDate: formatLocalDate(now), toDate: formatLocalDate(to) };
}

export default function HomeScreen() {
  const [events, setEvents] = useState<EventExpectation[] | null>(null);
  const [statuses, setStatuses] = useState<Record<string, EventStatus>>({});
  // Raw tracked-or-not calendar_events rows for the current window - a
  // separate store from EventExpectation, see
  // trading_system/calendar_repository.py. Filtered/deduped for display via
  // the trackedCalendarEvents memo below, not here, since that filtering
  // depends on `events` too and the two fetches now resolve independently
  // of each other (see loadEvents()).
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestLoadId = useRef(0);

  const loadEvents = useCallback(async () => {
    const loadId = ++latestLoadId.current;
    setError(null);

    // Started here, before getEvents() is awaited below - a slow or
    // failing getEvents() must never delay getUpcomingCalendarEvents() from
    // even starting, and vice versa. Same principle as events/upcoming.tsx's
    // load(), which fires both requests before awaiting either.
    const { fromDate, toDate } = deviceLocalCalendarWindow();
    const calendarPromise = getUpcomingCalendarEvents(fromDate, toDate)
      .then((list) => {
        if (loadId !== latestLoadId.current) return;
        setCalendarEvents(list);
      })
      .catch(() => {
        if (loadId !== latestLoadId.current) return;
        // Leave the previous list (or null) in place rather than clearing
        // it to [] - a failed fetch must not render identically to "no
        // tracked calendar events".
      });

    try {
      const list = await getEvents();
      if (loadId !== latestLoadId.current) return;
      setEvents(list);
      setStatuses({});

      // The card list renders immediately from `list` above. Each event's
      // paper-status is then fetched independently, not combined into one
      // batched wait, so one slow or failing request can't hold up every
      // other card - each card updates for itself as its own status
      // arrives, instead of the whole screen waiting on the slowest of N.
      list.forEach((event) => {
        getPaperStatus(event.event_id)
          .then((status) => {
            if (loadId !== latestLoadId.current) return;
            setStatuses((prev) => ({
              ...prev,
              [event.event_id]: { run: status.paper_run, statusError: false },
            }));
          })
          .catch(() => {
            if (loadId !== latestLoadId.current) return;
            setStatuses((prev) => ({
              ...prev,
              [event.event_id]: { run: null, statusError: true },
            }));
          });
      });
    } catch (err) {
      if (loadId !== latestLoadId.current) return;
      setError(err instanceof Error ? err.message : 'Tuntematon virhe');
    }

    await calendarPromise;
  }, []);

  // Tracked calendar rows for display: status = 'tracked' only, and never
  // an occurrence already tracked through the real trading system - same
  // de-dupe as events/upcoming.tsx's mergeUpcomingRows(), keyed on
  // instrument + scheduled_date and scoped to 'earnings' calendar rows only
  // (EventExpectation has no explicit event_type of its own, so a
  // non-earnings candidate for the same instrument/date is a different
  // occurrence and must never be dropped by this).
  const trackedCalendarEvents = useMemo(() => {
    if (!calendarEvents) return null;
    const trackedEarningsOccurrences = new Set(
      (events ?? []).map((event) => `${event.instrument.toUpperCase()}|${event.scheduled_date}`),
    );
    return calendarEvents.filter((event) => {
      if (event.status !== 'tracked') return false;
      if (event.event_type !== 'earnings') return true;
      const key = `${event.instrument.toUpperCase()}|${event.scheduled_date}`;
      return !trackedEarningsOccurrences.has(key);
    });
  }, [calendarEvents, events]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadEvents();
    }, 0);

    return () => clearTimeout(timer);
  }, [loadEvents]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadEvents();
    setRefreshing(false);
  }, [loadEvents]);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          tintColor="#8a96a8"
        />
      }
    >
      <View style={styles.header}>
        <View>
          <Text style={styles.brand}>MarketAI</Text>
          <Text style={styles.subtitle}>Tulosjulkaisut</Text>
        </View>

        <View style={styles.paperBadge}>
          <View style={styles.statusDot} />
          <Text style={styles.paperText}>PAPER</Text>
        </View>
      </View>

      <Text style={styles.sectionTitle}>SEURANNASSA</Text>

      {!events && !error ? (
        <ActivityIndicator color="#8a96a8" style={styles.loader} />
      ) : null}

      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      {events && events.length === 0 && trackedCalendarEvents && trackedCalendarEvents.length === 0 ? (
        <View style={styles.emptyCard}>
          <Text style={styles.emptyText}>
            Ei vielä seurattavia tulosjulkaisuja.
          </Text>
        </View>
      ) : null}

      {events?.map((event) => (
        <EventCard key={event.event_id} event={event} status={statuses[event.event_id]} />
      ))}

      {trackedCalendarEvents?.map((event) => (
        <CalendarEventCard key={event.calendar_event_id} event={event} />
      ))}

      <Pressable
        style={styles.upcomingButton}
        onPress={() => router.push('/events/upcoming')}
      >
        <Text style={styles.upcomingButtonText}>Kaikki tulevat julkaisut</Text>
        <Text style={styles.upcomingButtonChevron}>→</Text>
      </Pressable>

      <Text style={styles.footer}>MarketAI • vain PAPER-kaupankäynti</Text>
    </ScrollView>
  );
}

function EventCard({ event, status }: { event: EventExpectation; status?: EventStatus }) {
  const run = status?.run ?? null;
  // A run computed against an older expectation version (the event was
  // edited afterwards) must not present its status/direction/confidence as
  // current here either - the detail screen already warns about this once
  // opened, but the card is what the user sees first.
  const isStale =
    run?.expectation_version !== undefined && run.expectation_version !== event.version;
  const statusText = !status
    ? 'Ladataan...'
    : isStale
      ? 'Vanhentunut analyysi'
      : describeStatus(status.run, status.statusError);
  const scheduled = new Date(`${event.scheduled_date}T12:00:00`);
  const dateText = Number.isNaN(scheduled.getTime())
    ? event.scheduled_date
    : scheduled.toLocaleDateString('fi-FI');
  const strategy = isStale ? null : (run?.strategy ?? null);

  return (
    <Link href={{ pathname: '/events/[eventId]', params: { eventId: event.event_id } }} asChild>
      <Pressable style={styles.eventCard}>
        <View style={styles.rowBetween}>
          <View style={styles.eventCardTitleBlock}>
            <Text style={styles.company} numberOfLines={1}>
              {event.event_name}
            </Text>
            <Text style={styles.symbol}>{event.instrument}</Text>
          </View>
          <Text style={styles.dateText}>{dateText}</Text>
        </View>

        <View style={styles.statusRow}>
          <Text style={styles.statusText}>{statusText}</Text>
          {strategy ? (
            <Text style={styles.strategyText}>
              {localizeDirection(strategy.direction)}
              {strategy.confidence !== undefined ? ` · ${strategy.confidence}` : ''}
            </Text>
          ) : null}
        </View>
      </Pressable>
    </Link>
  );
}

// Tracked calendar_events row - a separate store from EventExpectation (see
// trading_system/calendar_repository.py). This card is deliberately
// read-only and only ever shows the fixed "Seurannassa" status text, never
// an EventExpectation-style status (e.g. "Analysoitu", "Odottaa
// vahvistusta") - a calendar_events row has no paper-run/strategy state of
// its own to describe.
function CalendarEventCard({ event }: { event: CalendarEvent }) {
  const scheduled = new Date(`${event.scheduled_date}T12:00:00`);
  const dateText = Number.isNaN(scheduled.getTime())
    ? event.scheduled_date
    : scheduled.toLocaleDateString('fi-FI');

  return (
    <View style={styles.eventCard}>
      <View style={styles.rowBetween}>
        <View style={styles.eventCardTitleBlock}>
          <Text style={styles.company} numberOfLines={1}>
            {event.company_name}
          </Text>
          <Text style={styles.symbol}>{event.instrument}</Text>
        </View>
        <Text style={styles.dateText}>{dateText}</Text>
      </View>

      <View style={styles.trackedBadge}>
        <Text style={styles.trackedBadgeText}>Seurannassa</Text>
      </View>
    </View>
  );
}

function describeStatus(run: PaperRun | null, statusError: boolean): string {
  if (statusError) return 'Tila ei saatavilla';
  if (!run) return 'Odottaa julkaisua';
  switch (run.status) {
    case 'waiting_confirmation':
      return 'Odottaa vahvistusta';
    case 'expired_no_trade':
      return 'Vanhentui – ei kauppaa';
    case 'paper_executed':
      return 'Paperikauppa toteutettu';
    default:
      return 'Käsitellään';
  }
}

function localizeDirection(direction?: string) {
  if (direction === 'LONG') return 'OSTO';
  if (direction === 'SHORT') return 'MYYNTI';
  return 'EI KAUPPAA';
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
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 26,
  },
  brand: {
    color: '#f4f7fb',
    fontSize: 30,
    fontWeight: '800',
    letterSpacing: -0.8,
  },
  subtitle: {
    color: '#727b8b',
    fontSize: 13,
    marginTop: 2,
  },
  paperBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#172219',
    borderWidth: 1,
    borderColor: '#28492f',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    gap: 7,
  },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: '#48c76a',
  },
  paperText: {
    color: '#72db8b',
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 1,
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
    marginBottom: 16,
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
    fontSize: 17,
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
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 12,
  },
  statusText: {
    color: '#d7ad5f',
    fontSize: 13,
    fontWeight: '600',
  },
  strategyText: {
    color: '#72b8db',
    fontSize: 13,
    fontWeight: '700',
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
  upcomingButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 14,
    paddingVertical: 15,
    marginTop: 8,
  },
  upcomingButtonText: {
    color: '#d9dee7',
    fontSize: 14,
    fontWeight: '700',
  },
  upcomingButtonChevron: {
    color: '#8a96a8',
    fontSize: 14,
    fontWeight: '700',
  },
  footer: {
    color: '#4e5868',
    fontSize: 11,
    textAlign: 'center',
    marginTop: 28,
  },
});
