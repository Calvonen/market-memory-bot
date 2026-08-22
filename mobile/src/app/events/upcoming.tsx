import { Link } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
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
  untrackCalendarEvent,
} from '@/services/api';

// Merges the real MarketAI tracking store (GET /api/v1/events) with the
// earnings-calendar candidate/watchlist store (GET /api/v1/calendar/upcoming).
// A calendar candidate/tracked row never influences trading by itself - see
// trading_system/calendar_repository.py - it only ever adds "things worth
// noticing", with an explicit "Lisää seurantaan" action per card.

// Calendar range MVP: 7/30 days only, default 7 - no 90/180-day or "Kaikki"
// option. The backend mirrors this exactly: GET /api/v1/calendar/upcoming
// defaults to and rejects anything beyond 30 days
// (MAX_CALENDAR_LOOKAHEAD_DAYS in trading_system/api.py), which is also
// why load() below never needs to pass an explicit to_date - the API's
// default window already covers the widest chip here.
const DATE_RANGES = [
  { label: '7 pv', days: 7 },
  { label: '30 pv', days: 30 },
] as const;

const MS_PER_DAY = 24 * 60 * 60 * 1000;

// Calendar dates in this MVP carry no time-of-day (see the minimal-schema
// note in docs/calendar_watchlist.md) - every comparison must work purely
// on the Y/M/D calendar date, never on a noon/midnight timestamp. Parsing
// a date at noon and comparing it against a midnight cutoff silently
// excludes the cutoff day itself (an event exactly N days out sorts after
// a midnight-of-day-N cutoff), which is exactly what made the range chips
// exclusive instead of inclusive. Date.UTC() here is only a stable
// arithmetic base for turning Y/M/D into a comparable integer "day
// number" - the Y/M/D components themselves already come from local
// values, so this never reinterprets a date in UTC calendar terms.
function dateOnlyOrdinal(year: number, monthIndex: number, day: number): number {
  return Date.UTC(year, monthIndex, day) / MS_PER_DAY;
}

function parseDateOnlyOrdinal(dateStr: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!match) return null;
  const [, year, month, day] = match;
  return dateOnlyOrdinal(Number(year), Number(month) - 1, Number(day));
}

function formatDateOnly(year: number, monthIndex: number, day: number): string {
  const mm = String(monthIndex + 1).padStart(2, '0');
  const dd = String(day).padStart(2, '0');
  return `${year}-${mm}-${dd}`;
}

function ordinalToDateOnly(ordinal: number): string {
  // Date.UTC()/MS_PER_DAY produced this ordinal (see dateOnlyOrdinal()
  // above), so the exact inverse reads it back with the UTC getters, not
  // the local ones - this never depends on, or reintroduces, the device's
  // own UTC offset.
  const asDate = new Date(ordinal * MS_PER_DAY);
  return formatDateOnly(asDate.getUTCFullYear(), asDate.getUTCMonth(), asDate.getUTCDate());
}

type DeviceLocalDateWindow = {
  fromDate: string;
  toDate: string;
  todayOrdinal: number;
  toOrdinal: number;
};

// P2 regression: GET /api/v1/calendar/upcoming must never be called
// parameter-free and left to default to the *backend host's* own
// date.today() - a device whose local calendar day has already rolled
// over (or hasn't yet) relative to the host's clock/timezone would then
// have its window computed against the wrong day. This is the single
// place "today" and the range window are derived from the device's own
// clock (getFullYear/getMonth/getDate - never toISOString(), which is
// UTC) - load() sends the result as explicit fromDate/toDate query
// params, and the filtered useMemo below reuses the exact same
// todayOrdinal/toOrdinal for its 7/30 pv cutoff, so the network window
// and the client-side window can never disagree with each other, only
// (correctly) drift from the backend host's own clock, which still
// independently enforces its own MAX_CALENDAR_LOOKAHEAD_DAYS cap
// regardless of what a client sends (trading_system/api.py) - this is
// about which *day* the window starts from, not a trust boundary.
function deviceLocalDateWindow(
  rangeDays: number,
  referenceDate: Date = new Date(),
): DeviceLocalDateWindow {
  const todayOrdinal = dateOnlyOrdinal(
    referenceDate.getFullYear(),
    referenceDate.getMonth(),
    referenceDate.getDate(),
  );
  const toOrdinal = todayOrdinal + rangeDays;
  return {
    fromDate: formatDateOnly(
      referenceDate.getFullYear(),
      referenceDate.getMonth(),
      referenceDate.getDate(),
    ),
    toDate: ordinalToDateOnly(toOrdinal),
    todayOrdinal,
    toOrdinal,
  };
}

// Short, on-screen market codes only - long country names and raw
// calendar/provider values never reach the UI, so a market column fits on
// one line.
type MarketCode = 'FI' | 'SE' | 'DE' | 'US' | 'UK' | 'DK' | 'NO';

// Calendar/provider market values arrive in inconsistent shapes (ISO-ish
// codes, exchange names, English/Finnish country names). This collapses
// every known variant down to its short code.
const MARKET_ALIASES: Record<string, MarketCode> = {
  US: 'US',
  USA: 'US',
  NYSE: 'US',
  NASDAQ: 'US',
  YHDYSVALLAT: 'US',
  UK: 'UK',
  GB: 'UK',
  'ISO-BRITANNIA': 'UK',
  FI: 'FI',
  FINLAND: 'FI',
  SUOMI: 'FI',
  SE: 'SE',
  SWEDEN: 'SE',
  RUOTSI: 'SE',
  DE: 'DE',
  GERMANY: 'DE',
  SAKSA: 'DE',
  DK: 'DK',
  DENMARK: 'DK',
  TANSKA: 'DK',
  NO: 'NO',
  NORWAY: 'NO',
  NORJA: 'NO',
};

// Ticker suffix -> market code, used whenever the market itself is
// missing or unrecognized (e.g. calendar/provider "Unknown"). No suffix is
// the actual USA convention on this backend (e.g. "AAPL"); an unrecognized
// suffix (".PA", ".AS", ".SW", ...) must not be guessed - it falls back to
// 'Muu' instead of a wrong market code.
function marketFromInstrumentSuffix(instrument: string): MarketCode | 'Muu' {
  if (!instrument.includes('.')) {
    return 'US';
  }
  const suffix = instrument.split('.').pop()?.toUpperCase() ?? '';
  switch (suffix) {
    case 'HE':
      return 'FI';
    case 'ST':
      return 'SE';
    case 'DE':
      return 'DE';
    case 'L':
      return 'UK';
    case 'CO':
      return 'DK';
    case 'OL':
      return 'NO';
    default:
      return 'Muu';
  }
}

function marketForInstrument(instrument: string): MarketCode | 'Muu' {
  return marketFromInstrumentSuffix(instrument);
}

// Normalizes a calendar/provider market value to one of the short
// user-facing codes. The ticker-suffix fallback (marketFromInstrumentSuffix)
// is only ever used when the market itself is missing/empty or "Unknown" -
// an explicit but unrecognized market value (e.g. a new exchange name this
// table doesn't know yet) must never be guessed from the ticker, it becomes
// 'Muu' instead - guessing from the ticker there could contradict a real,
// just-unmapped market value the provider actually sent.
function normalizeMarket(rawMarket: string, instrument: string): MarketCode | 'Muu' {
  const normalized = rawMarket.trim().toUpperCase();
  const known = MARKET_ALIASES[normalized];
  if (known) return known;
  if (normalized === '' || normalized === 'UNKNOWN') {
    return marketFromInstrumentSuffix(instrument);
  }
  return 'Muu';
}

export type UpcomingRow = {
  key: string;
  companyName: string;
  instrument: string;
  market: MarketCode | 'Muu';
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
      market: normalizeMarket(event.market, event.instrument),
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
  const todayOrdinal = dateOnlyOrdinal(now.getFullYear(), now.getMonth(), now.getDate());

  return [...expectationRows, ...calendarRows].sort((a, b) => {
    const aOrdinal = parseDateOnlyOrdinal(a.scheduledDate);
    const bOrdinal = parseDateOnlyOrdinal(b.scheduledDate);
    const aPast = aOrdinal === null || aOrdinal < todayOrdinal;
    const bPast = bOrdinal === null || bOrdinal < todayOrdinal;
    if (aPast !== bPast) return aPast ? 1 : -1;
    const aSort = aOrdinal ?? 0;
    const bSort = bOrdinal ?? 0;
    return aPast ? bSort - aSort : aSort - bSort;
  });
}

type CalendarOverride = { event: CalendarEvent; version: number };

// P2 regression: onTrack() used to bump the same generation counter
// load() uses for its own overlap guard, so tracking one candidate
// discarded an *entire* still-pending calendar response wholesale - e.g.
// switch the "7 pv" chip to "30 pv" (a wider, still-in-flight request),
// track a candidate visible in the old 7 pv data, and every row the 30 pv
// response was about to bring in got silently dropped, not just the
// tracked row protected. Row-scoped versioning fixes this: each
// successful track records only its own row's override, tagged with a
// monotonically increasing version; a calendar response - whichever
// request it came from - merges those overrides back in only for
// mutations that happened *after* that particular request started, so a
// pending response is never thrown away wholesale.
function applyLocalCalendarOverrides(
  list: CalendarEvent[],
  overrides: Map<string, CalendarOverride>,
  versionAtRequestStart: number,
): CalendarEvent[] {
  return list.map((event) => {
    const override = overrides.get(event.calendar_event_id);
    if (override && override.version > versionAtRequestStart) {
      return override.event;
    }
    return event;
  });
}

export default function UpcomingEventsScreen() {
  const [events, setEvents] = useState<EventExpectation[] | null>(null);
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // P2 regression: a failed calendar GET must never be flattened into
  // setCalendarEvents([]) - that renders identically to a genuine
  // "zero candidates in range" response, so a real outage silently looks
  // like "Ei julkaisuja" instead of a visible error. calendarError is its
  // own state, entirely separate from `error` (which is events/
  // expectation-only) - a calendar failure never touches `calendarEvents`
  // itself, so whatever was last successfully loaded stays on screen
  // (stale-but-real data) instead of being wiped by the failure.
  const [calendarError, setCalendarError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [trackingIds, setTrackingIds] = useState<Set<string>>(new Set());
  const [trackError, setTrackError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  // 'Kaikki' here is the market/country filter's own option (data-derived,
  // see `markets` below) - unrelated to, and independent of, the date
  // range below, which deliberately has no "Kaikki" of its own.
  const [market, setMarket] = useState<string>('Kaikki');
  const [rangeDays, setRangeDays] = useState<number>(7);
  // Separate generation counters for the two independent sources - never a
  // single shared one. These still guard purely against *overlapping
  // load() calls* on the same source (e.g. a quick double pull-to-refresh,
  // or a range-chip change firing a new calendar request before the
  // previous one settled) - an older response losing to a newer one is
  // correct there, since the newer request supersedes it entirely. A
  // single-row track mutation is a different kind of "newer" - see
  // localCalendarOverrides/mutationVersion below - and must never trigger
  // that same wholesale discard of an otherwise-current pending response.
  const latestEventsLoadId = useRef(0);
  const latestCalendarLoadId = useRef(0);
  // Row-scoped track-mutation protection (see applyLocalCalendarOverrides()
  // above) - never a blunt "invalidate the whole calendar generation"
  // bump. mutationVersion increments once per successful track; each
  // override records the version it was made at, so any calendar request
  // that started before that point (and therefore can still arrive with a
  // pre-track snapshot of that one row) knows to keep the override instead
  // of reverting it, without discarding anything else that response
  // brings.
  const localCalendarOverrides = useRef<Map<string, CalendarOverride>>(new Map());
  const mutationVersion = useRef(0);

  const load = useCallback((currentRangeDays: number) => {
    // Guard against overlapping load() calls (e.g. pull-to-refresh fired
    // again while the first requests are still pending): an older response
    // resolving after a newer one must never replace the already refreshed
    // lists, or set an obsolete error over a successful refresh. Both
    // generations are bumped together here, so two overlapping load()
    // calls still invalidate each other's responses on both sources, same
    // as before this split.
    const eventsLoadId = ++latestEventsLoadId.current;
    const calendarLoadId = ++latestCalendarLoadId.current;
    // Snapshot of the mutation version at the moment this request started -
    // any track that completes *after* this point (while this request is
    // still in flight) must win over whatever this response eventually
    // brings back for that one row; see applyLocalCalendarOverrides() above.
    const calendarVersionAtStart = mutationVersion.current;
    setError(null);
    // Optimistically cleared here too, same as setError(null) above - a
    // retry (pull-to-refresh, or the effect below re-firing on a range
    // change) must not keep showing a stale error banner while the new
    // attempt is in flight; it's set again below if this attempt also
    // fails.
    setCalendarError(null);

    // Device-local window (see deviceLocalDateWindow() above) sent
    // explicitly - never a parameter-free GET left to the backend host's
    // own date.today(). currentRangeDays is passed in rather than closed
    // over so this callback's identity stays stable across re-renders;
    // the mount effect below re-invokes it with the latest rangeDays
    // whenever the 7/30 pv chip changes.
    const { fromDate, toDate } = deviceLocalDateWindow(currentRangeDays);

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

    const calendarPromise = getUpcomingCalendarEvents(fromDate, toDate)
      .then((list) => {
        if (calendarLoadId !== latestCalendarLoadId.current) return;
        setCalendarEvents(
          applyLocalCalendarOverrides(list, localCalendarOverrides.current, calendarVersionAtStart),
        );
      })
      .catch((err) => {
        if (calendarLoadId !== latestCalendarLoadId.current) return;
        // P2 regression: never setCalendarEvents([]) here - that would
        // render identically to a genuine "zero candidates in this
        // window" response, silently passing an outage off as an empty
        // calendar. calendarEvents is deliberately left untouched, so
        // whatever was last successfully loaded (or `null`, if this is
        // the very first attempt) stays exactly as it was; only the
        // error state changes, driving the calendar-specific error/retry
        // UI below instead of the plain "Ei julkaisuja" empty state.
        setCalendarError(
          err instanceof Error ? err.message : 'Kalenteritietoja ei juuri nyt saatu haettua.',
        );
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
    // Re-fires whenever the 7/30 pv chip changes, not just on mount - the
    // calendar GET's window depends on rangeDays (see load() above), so a
    // chip change must re-request the calendar list with the new window,
    // not just re-filter client-side over data limited to the old one.
    const timer = setTimeout(() => {
      void load(rangeDays);
    }, 0);
    return () => clearTimeout(timer);
  }, [load, rangeDays]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load(rangeDays);
    setRefreshing(false);
  }, [load, rangeDays]);

  const onTrack = useCallback(async (calendarEventId: string) => {
    setTrackError(null);
    setTrackingIds((prev) => new Set(prev).add(calendarEventId));
    try {
      const updated = await trackCalendarEvent(calendarEventId);
      // Records only this one row's override (P2 regression: this used to
      // be a blunt bump of the calendar generation counter, which discarded an
      // entire still-pending calendar response - e.g. a wider-range 30 pv
      // request already in flight when the user tracked a candidate
      // visible in the old 7 pv data - instead of protecting just this
      // row). Any calendar response whose own request started before this
      // version is recorded will apply it via applyLocalCalendarOverrides()
      // in load() above instead of reverting this row back to 'candidate';
      // a response for a request that started *after* this point already
      // reflects the tracked state itself and simply wins normally.
      const version = ++mutationVersion.current;
      localCalendarOverrides.current.set(calendarEventId, { event: updated, version });
      setCalendarEvents((prev) =>
        (prev ?? []).map((event) => (event.calendar_event_id === calendarEventId ? updated : event)),
      );
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

  const onUntrack = useCallback(async (calendarEventId: string) => {
    setTrackError(null);
    setTrackingIds((prev) => new Set(prev).add(calendarEventId));
    try {
      const updated = await untrackCalendarEvent(calendarEventId);
      // Same row-scoped override/mutationVersion mechanism as onTrack()
      // above - see the P2 regression note there for why this must never
      // be a blunt calendar-generation bump.
      const version = ++mutationVersion.current;
      localCalendarOverrides.current.set(calendarEventId, { event: updated, version });
      setCalendarEvents((prev) =>
        (prev ?? []).map((event) => (event.calendar_event_id === calendarEventId ? updated : event)),
      );
    } catch (err) {
      setTrackError(err instanceof Error ? err.message : 'Seurannan poisto epäonnistui');
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

  // Data-derived, never a hardcoded country list: whatever markets are
  // actually present across today's rows (expectation rows via
  // marketForInstrument(), calendar rows via their own explicit `market`
  // field), plus 'Kaikki'. Independent of the date-range filter below -
  // this list is built from the unfiltered `rows`, not `filtered`, so
  // picking a date range never changes which market options are offered.
  const markets = useMemo(() => {
    const unique = new Set(rows.map((row) => row.market));
    return ['Kaikki', ...Array.from(unique).sort()];
  }, [rows]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    // Same deviceLocalDateWindow() the calendar GET in load() uses above -
    // not a second, independently-derived "today". cutoffOrdinal (toOrdinal)
    // is today + rangeDays *inclusive*: an event exactly rangeDays out
    // (e.g. day 7 of the "7 pv" chip, matching the backend's own inclusive
    // lte("scheduled_date", ...) window) must still be included, not
    // excluded by a day-level off-by-one.
    const { todayOrdinal, toOrdinal: cutoffOrdinal } = deviceLocalDateWindow(rangeDays);

    // Market and date range apply independently - each is its own `if`
    // against the unfiltered row, neither one's outcome depends on the
    // other's current value.
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
      const scheduledOrdinal = parseDateOnlyOrdinal(row.scheduledDate);
      if (
        scheduledOrdinal === null ||
        scheduledOrdinal < todayOrdinal ||
        scheduledOrdinal > cutoffOrdinal
      ) {
        return false;
      }
      return true;
    });
  }, [rows, market, search, rangeDays]);

  // Clears the moment either source has settled - with data OR an error -
  // same "first source to settle wins" policy as everywhere else on this
  // screen (see load() above). A calendar failure is a settled state, not
  // a still-loading one, so calendarError must clear this exactly like
  // calendarEvents having data would.
  const loading = !events && !calendarEvents && !error && !calendarError;

  // P2 regression: a candidate/tracked calendar result set can run into
  // the hundreds - eagerly rendering every card via a plain ScrollView
  // (as this used to) mounts and lays out all of them up front, even the
  // ones scrolled far out of view. FlatList only ever mounts what's near
  // the viewport, mounting/unmounting cards as the list scrolls, however
  // large `filtered` grows.
  const renderRow = useCallback(
    ({ item: row }: { item: UpcomingRow }) => {
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
          {row.status === 'tracked' && row.kind === 'calendar' ? (
            <Pressable
              style={styles.trackButton}
              disabled={trackingIds.has(row.calendarEventId ?? '')}
              onPress={() => row.calendarEventId && void onUntrack(row.calendarEventId)}
            >
              <Text style={styles.trackButtonText}>
                {trackingIds.has(row.calendarEventId ?? '') ? 'Poistetaan…' : 'Poista seurannasta'}
              </Text>
            </Pressable>
          ) : row.status === 'tracked' ? (
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
            href={{ pathname: '/events/[eventId]', params: { eventId: row.eventId } }}
            asChild
          >
            <Pressable style={styles.eventCard}>{cardBody}</Pressable>
          </Link>
        );
      }

      return <View style={styles.eventCard}>{cardBody}</View>;
    },
    [onTrack, onUntrack, trackingIds],
  );

  const listHeader = (
    <>
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

      {calendarError ? (
        <View style={styles.calendarErrorCard}>
          <Text style={styles.errorText}>{calendarError}</Text>
          <Text style={styles.calendarErrorHint}>
            Seuratut MarketAI-eventit yllä toimivat silti normaalisti.
          </Text>
          <Pressable style={styles.retryButton} onPress={() => void load(rangeDays)}>
            <Text style={styles.retryButtonText}>Yritä uudelleen</Text>
          </Pressable>
        </View>
      ) : null}

      {/* P2 regression: this must never show while a calendar fetch has
          actually failed (calendarError) - "Ei julkaisuja" reads as a
          successful, genuinely empty response, which a failed request is
          not. The calendarErrorCard above is what a calendar failure gets
          instead; this empty state is reserved for a real, successful
          empty result. */}
      {!loading && !calendarError && filtered.length === 0 ? (
        <View style={styles.emptyCard}>
          <Text style={styles.emptyText}>Ei julkaisuja valituilla suodattimilla.</Text>
        </View>
      ) : null}
    </>
  );

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#8a96a8" />
      }
      data={filtered}
      keyExtractor={(row) => row.key}
      renderItem={renderRow}
      ListHeaderComponent={listHeader}
    />
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
