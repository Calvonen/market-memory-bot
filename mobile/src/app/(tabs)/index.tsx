import { Link, router, useFocusEffect } from 'expo-router';
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

import { TrackedEventDetails, TrackedEventsSection } from '@/components/TrackedEventsSection';
import {
  CalendarEvent,
  EventExpectation,
  getEvents,
  getPaperStatus,
  getUpcomingCalendarEvents,
  PaperRun,
} from '@/services/api';
import { getTrackedEventActivities, TrackedMarketEvent } from '@/services/tracked-events';

type EventStatus = { run: PaperRun | null; statusError: boolean };
type TrackedActivityState = 'loading' | 'active' | 'inactive' | 'error';
type TrackedEventSnapshot = {
  count: number;
  eventIds: string[];
  calendarEventIds: string[];
  statusByCalendarEventId: Record<string, string>;
  eventByCalendarEventId: Record<string, TrackedMarketEvent>;
};

const CALENDAR_WINDOW_DAYS = 30;
const PARENT_REFRESH_TIMEOUT_MS = 10_000;

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

function isTrackedExpectation(eventId: string): boolean {
  return eventId.startsWith('tracked:') || eventId.startsWith('calendar:');
}

function isExpectationBackedByLoadedTrackedEvent(
  eventId: string,
  loadedTrackedEventIds: ReadonlySet<string>,
  loadedCalendarEventIds: ReadonlySet<string>,
): boolean {
  if (eventId.startsWith('tracked:')) {
    const trackedEventId = eventId.slice('tracked:'.length);
    return Boolean(trackedEventId) && loadedTrackedEventIds.has(trackedEventId);
  }
  if (eventId.startsWith('calendar:')) {
    const calendarEventId = eventId.slice('calendar:'.length);
    return Boolean(calendarEventId) && loadedCalendarEventIds.has(calendarEventId);
  }
  return false;
}

function isHomeExpectationVisible(
  event: EventExpectation,
  status: EventStatus | undefined,
  loadedTrackedEventIds: ReadonlySet<string>,
  loadedCalendarEventIds: ReadonlySet<string>,
  trackedActivity: TrackedActivityState | undefined,
  canonicalSnapshotReady: boolean,
): boolean {
  const trackedExpectation = isTrackedExpectation(event.event_id);
  if (trackedExpectation && !canonicalSnapshotReady) return false;

  if (event.event_id.startsWith('tracked:')) {
    const trackedEventId = event.event_id.slice('tracked:'.length);
    if (!trackedEventId) return false;
    if (loadedTrackedEventIds.has(trackedEventId)) return false;
  }

  if (event.event_id.startsWith('calendar:')) {
    const calendarEventId = event.event_id.slice('calendar:'.length);
    if (!calendarEventId) return false;
    if (loadedCalendarEventIds.has(calendarEventId)) return true;
  }

  if (trackedExpectation) {
    if (!trackedActivity || trackedActivity === 'loading') return false;
    if (trackedActivity === 'inactive') return false;
  }

  const today = formatLocalDate(new Date());
  if (event.scheduled_date >= today) return true;

  if (trackedExpectation) return true;

  if (!status || status.statusError) return true;

  return status.run?.status === 'waiting_confirmation';
}

export default function HomeScreen() {
  const [events, setEvents] = useState<EventExpectation[] | null>(null);
  const [statuses, setStatuses] = useState<Record<string, EventStatus>>({});
  const [trackedActivityByEventId, setTrackedActivityByEventId] = useState<
    Record<string, TrackedActivityState>
  >({});
  const [trackedActivityError, setTrackedActivityError] = useState<string | null>(null);
  const [activityRetryToken, setActivityRetryToken] = useState(0);
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[] | null>(null);
  const [trackedEventCount, setTrackedEventCount] = useState<number | null>(null);
  const [persistentEventIds, setPersistentEventIds] = useState<Set<string>>(() => new Set());
  const [persistentCalendarEventIds, setPersistentCalendarEventIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [persistentStatusByCalendarEventId, setPersistentStatusByCalendarEventId] = useState<
    Record<string, string>
  >({});
  const [persistentEventByCalendarEventId, setPersistentEventByCalendarEventId] = useState<
    Record<string, TrackedMarketEvent>
  >({});
  const [trackedRefreshToken, setTrackedRefreshToken] = useState(0);
  const currentTrackedRefreshToken = useRef(0);
  const nextTrackedRefreshToken = useRef(0);
  const trackedRefreshWaiters = useRef(new Map<number, () => void>());
  const [calendarError, setCalendarError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestLoadId = useRef(0);

  const loadEvents = useCallback((resetCanonical = false) => {
    const loadId = ++latestLoadId.current;
    setError(null);
    setCalendarError(null);
    setCalendarEvents(null);
    if (resetCanonical) {
      setTrackedActivityError(null);
      setTrackedActivityByEventId({});
      setTrackedEventCount(null);
      setPersistentEventIds(new Set());
      setPersistentCalendarEventIds(new Set());
      setPersistentStatusByCalendarEventId({});
      setPersistentEventByCalendarEventId({});
    }

    const eventsPromise = getEvents()
      .then((list) => {
        if (loadId !== latestLoadId.current) return;
        setError(null);
        setEvents(list);
        setStatuses({});

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
      })
      .catch((err) => {
        if (loadId !== latestLoadId.current) return;
        setError(err instanceof Error ? err.message : 'Tuntematon virhe');
      });

    const { fromDate, toDate } = deviceLocalCalendarWindow();
    const calendarPromise = getUpcomingCalendarEvents(fromDate, toDate)
      .then((list) => {
        if (loadId !== latestLoadId.current) return;
        setCalendarError(null);
        setCalendarEvents(list);
      })
      .catch((err) => {
        if (loadId !== latestLoadId.current) return;
        setCalendarError(
          err instanceof Error ? err.message : 'Kalenteritietoja ei juuri nyt saatu haettua.',
        );
      });

    return new Promise<void>((resolve) => {
      let eventsSettled = false;
      let calendarSettled = false;
      let completed = false;
      let settledCount = 0;

      const finish = () => {
        if (completed) return;
        completed = true;
        clearTimeout(timeout);
        resolve();
      };
      const markSettled = () => {
        settledCount += 1;
        if (settledCount === 2) finish();
      };

      void eventsPromise.finally(() => {
        eventsSettled = true;
        markSettled();
      });
      void calendarPromise.finally(() => {
        calendarSettled = true;
        markSettled();
      });

      const timeout = setTimeout(() => {
        if (completed) return;
        if (loadId === latestLoadId.current) {
          if (!eventsSettled) {
            setError('Tapahtumatietojen päivitys aikakatkaistiin. Yritä uudelleen.');
          }
          if (!calendarSettled) {
            setCalendarError('Kalenteritietojen päivitys aikakatkaistiin. Yritä uudelleen.');
          }
        }
        finish();
      }, PARENT_REFRESH_TIMEOUT_MS);
    });
  }, []);

  const handleTrackedEventSnapshot = useCallback((snapshot: TrackedEventSnapshot) => {
    setTrackedEventCount(snapshot.count);
    setPersistentEventIds(new Set(snapshot.eventIds));
    setPersistentCalendarEventIds(new Set(snapshot.calendarEventIds));
    setPersistentStatusByCalendarEventId(snapshot.statusByCalendarEventId);
    setPersistentEventByCalendarEventId(snapshot.eventByCalendarEventId);
  }, []);

  const handleCurrentTrackedEventSnapshot = useCallback(
    (snapshot: TrackedEventSnapshot) => {
      if (trackedRefreshToken !== currentTrackedRefreshToken.current) return;
      handleTrackedEventSnapshot(snapshot);
    },
    [handleTrackedEventSnapshot, trackedRefreshToken],
  );

  useEffect(() => {
    if (!events || trackedEventCount === null) return;

    const candidates = events.filter(
      (event) =>
        isTrackedExpectation(event.event_id) &&
        !isExpectationBackedByLoadedTrackedEvent(
          event.event_id,
          persistentEventIds,
          persistentCalendarEventIds,
        ),
    );
    const candidateIds = candidates.map((event) => event.event_id);
    const loadingState = Object.fromEntries(
      candidateIds.map((eventId) => [eventId, 'loading' as TrackedActivityState]),
    );
    let active = true;
    if (candidateIds.length === 0) {
      void Promise.resolve().then(() => {
        if (!active) return;
        setTrackedActivityError(null);
        setTrackedActivityByEventId({});
      });
      return () => {
        active = false;
      };
    }

    void Promise.resolve()
      .then(() => {
        if (!active) return null;
        setTrackedActivityError(null);
        setTrackedActivityByEventId(loadingState);
        return getTrackedEventActivities(candidateIds);
      })
      .then((activityByOccurrenceId) => {
        if (!active || !activityByOccurrenceId) return;
        setTrackedActivityError(null);
        setTrackedActivityByEventId(
          Object.fromEntries(
            candidateIds.map((eventId) => [
              eventId,
              activityByOccurrenceId[eventId]?.active ? 'active' : 'inactive',
            ]),
          ),
        );
      })
      .catch(() => {
        if (!active) return;
        setTrackedActivityError(
          'Seurantatilaa ei juuri nyt saatu varmistettua. Mahdolliset aktiiviset seurannat pidetään näkyvissä.',
        );
        setTrackedActivityByEventId(
          Object.fromEntries(candidateIds.map((eventId) => [eventId, 'error'])),
        );
      });

    return () => {
      active = false;
    };
  }, [
    events,
    trackedEventCount,
    persistentEventIds,
    persistentCalendarEventIds,
    activityRetryToken,
  ]);

  const visibleEvents = useMemo(() => {
    if (!events) return null;
    return events.filter((event) =>
      isHomeExpectationVisible(
        event,
        statuses[event.event_id],
        persistentEventIds,
        persistentCalendarEventIds,
        trackedActivityByEventId[event.event_id],
        trackedEventCount !== null,
      ),
    );
  }, [
    events,
    statuses,
    persistentEventIds,
    persistentCalendarEventIds,
    trackedActivityByEventId,
    trackedEventCount,
  ]);

  const expectationCalendarEventIds = useMemo(() => {
    const ids = new Set<string>();
    for (const event of visibleEvents ?? []) {
      if (!event.event_id.startsWith('calendar:')) continue;
      const calendarEventId = event.event_id.slice('calendar:'.length);
      if (calendarEventId) ids.add(calendarEventId);
    }
    return ids;
  }, [visibleEvents]);

  const trackedCalendarEvents = useMemo(() => {
    if (!calendarEvents || trackedEventCount === null) return null;
    const trackedEarningsOccurrences = new Set(
      (visibleEvents ?? []).map((event) => `${event.instrument.toUpperCase()}|${event.scheduled_date}`),
    );
    const expectationIds = new Set((events ?? []).map((event) => event.event_id));
    return calendarEvents.filter((event) => {
      if (event.status !== 'tracked') return false;
      if (persistentCalendarEventIds.has(event.calendar_event_id)) return false;
      const canonicalOccurrenceId = `calendar:${event.calendar_event_id}`;
      const canonicalActivity = trackedActivityByEventId[canonicalOccurrenceId];
      if (
        expectationIds.has(canonicalOccurrenceId) &&
        (!canonicalActivity || canonicalActivity === 'loading')
      ) {
        return false;
      }
      if (canonicalActivity === 'inactive') return false;
      if (event.event_type !== 'earnings') return true;
      const key = `${event.instrument.toUpperCase()}|${event.scheduled_date}`;
      return !trackedEarningsOccurrences.has(key);
    });
  }, [
    calendarEvents,
    events,
    visibleEvents,
    persistentCalendarEventIds,
    trackedActivityByEventId,
    trackedEventCount,
  ]);

  useFocusEffect(
    useCallback(() => {
      const token = ++nextTrackedRefreshToken.current;
      currentTrackedRefreshToken.current = token;
      setTrackedActivityError(null);
      setTrackedActivityByEventId({});
      setTrackedEventCount(null);
      setPersistentEventIds(new Set());
      setPersistentCalendarEventIds(new Set());
      setPersistentStatusByCalendarEventId({});
      setPersistentEventByCalendarEventId({});
      setTrackedRefreshToken(token);

      const timer = setTimeout(() => {
        void loadEvents(true);
      }, 0);

      return () => clearTimeout(timer);
    }, [loadEvents]),
  );

  const handleTrackedRefreshSettled = useCallback((token: number) => {
    const resolve = trackedRefreshWaiters.current.get(token);
    if (!resolve) return;
    trackedRefreshWaiters.current.delete(token);
    resolve();
  }, []);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    const token = ++nextTrackedRefreshToken.current;
    currentTrackedRefreshToken.current = token;
    setTrackedActivityError(null);
    setTrackedActivityByEventId({});
    setTrackedEventCount(null);
    setPersistentEventIds(new Set());
    setPersistentCalendarEventIds(new Set());
    setPersistentStatusByCalendarEventId({});
    setPersistentEventByCalendarEventId({});
    const trackedRefresh = new Promise<void>((resolve) => {
      trackedRefreshWaiters.current.set(token, resolve);
    });
    setTrackedRefreshToken(token);
    await loadEvents();
    await trackedRefresh;
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

      {calendarError ? (
        <View style={styles.calendarErrorCard}>
          <Text style={styles.errorText}>{calendarError}</Text>
          <Text style={styles.calendarErrorHint}>
            Seuratut MarketAI-eventit yllä toimivat silti normaalisti.
          </Text>
          <Pressable style={styles.retryButton} onPress={() => void loadEvents()}>
            <Text style={styles.retryButtonText}>Yritä uudelleen</Text>
          </Pressable>
        </View>
      ) : null}

      {trackedActivityError ? (
        <View style={styles.calendarErrorCard}>
          <Text style={styles.errorText}>{trackedActivityError}</Text>
          <Text style={styles.calendarErrorHint}>
            Kortteja ei piiloteta ennen kuin canonical-seurannan tila voidaan varmistaa.
          </Text>
          <Pressable
            style={styles.retryButton}
            onPress={() => {
              setTrackedActivityError(null);
              setActivityRetryToken((value) => value + 1);
            }}
          >
            <Text style={styles.retryButtonText}>Yritä uudelleen</Text>
          </Pressable>
        </View>
      ) : null}

      {visibleEvents &&
      visibleEvents.length === 0 &&
      trackedEventCount === 0 &&
      trackedCalendarEvents &&
      trackedCalendarEvents.length === 0 &&
      !calendarError &&
      !trackedActivityError ? (
        <View style={styles.emptyCard}>
          <Text style={styles.emptyText}>Ei vielä seurattavia tulosjulkaisuja.</Text>
        </View>
      ) : null}

      {/* /api/v1/events used to render directly as events?.map; canonical Home now renders the filtered list below. */}
      {visibleEvents?.map((event) => {
        const calendarEventId = event.event_id.startsWith('calendar:')
          ? event.event_id.slice('calendar:'.length)
          : null;
        return (
          <EventCard
            key={`${event.event_id}:${trackedRefreshToken}`}
            event={event}
            status={statuses[event.event_id]}
            runtimeStatus={
              calendarEventId ? persistentStatusByCalendarEventId[calendarEventId] : undefined
            }
            trackedEvent={
              calendarEventId ? persistentEventByCalendarEventId[calendarEventId] : undefined
            }
            refreshToken={trackedRefreshToken}
          />
        );
      })}

      <TrackedEventsSection
        key={`tracked-events:${trackedRefreshToken}`}
        onSnapshot={handleCurrentTrackedEventSnapshot}
        excludeCalendarEventIds={expectationCalendarEventIds}
        refreshToken={trackedRefreshToken}
        onRefreshSettled={handleTrackedRefreshSettled}
      />

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

      <Pressable
        style={styles.upcomingButton}
        onPress={() => router.push('/history')}
      >
        <Text style={styles.upcomingButtonText}>Historia</Text>
        <Text style={styles.upcomingButtonChevron}>→</Text>
      </Pressable>

      <Text style={styles.footer}>MarketAI • vain PAPER-kaupankäynti</Text>
    </ScrollView>
  );
}

function EventCard({
  event,
  status,
  runtimeStatus,
  trackedEvent,
  refreshToken = 0,
}: {
  event: EventExpectation;
  status?: EventStatus;
  runtimeStatus?: string;
  trackedEvent?: TrackedMarketEvent;
  refreshToken?: number;
}) {
  const run = status?.run ?? null;
  const isStale =
    run?.expectation_version !== undefined && run.expectation_version !== event.version;
  const statusText = isStale
    ? 'Vanhentunut analyysi'
    : run
      ? describeStatus(run, false)
      : runtimeStatus
        ? runtimeStatus
        : !status
          ? 'Ladataan...'
          : describeStatus(null, status.statusError);
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

        {trackedEvent ? (
          <TrackedEventDetails event={trackedEvent} refreshToken={refreshToken} showSchedule />
        ) : null}
      </Pressable>
    </Link>
  );
}

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
  calendarErrorCard: {
    backgroundColor: '#1c1417',
    borderWidth: 1,
    borderColor: '#3a2226',
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
  },
  calendarErrorHint: {
    color: '#8994a6',
    fontSize: 12,
    marginTop: 4,
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