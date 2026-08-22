# Earnings calendar / watchlist (MVP)

A separate storage boundary for discovering and tracking upcoming events,
independent of the versioned `EventExpectationRepository` used by the
trading worker and PAPER pipeline.

## Lifecycle

```
candidate -> tracked -> research -> decision_to_prepare_strategy -> enrich_event_details -> preview -> approve
```

Only `candidate` and `tracked` are reachable in this PR. The remaining
stages exist as an enum (`CalendarEventStatus` in
`trading_system/calendar_repository.py`) so the storage shape does not need
to change again once research/strategy-preparation/enrichment are wired up
later. **`candidate` and `tracked` never influence the trading worker or the
PAPER pipeline** - only a future "approved" calendar event is meant to
graduate into an actual `market_events`/`EventExpectation` row, which is out
of scope here.

## Minimal schema

`company_name`, `instrument`, `market`, `event_type`, `scheduled_date`,
`source`, `occurrence_key`, `status` - deliberately no time-of-day, release
URL, consensus, or KPI data at the candidate/tracked stage. `event_type` is
a plain string, not hardcoded to `"earnings"` - a manually-entered event
(e.g. a production report no calendar provider tracks) is exactly as valid
a candidate.

## Provider interface

`EarningsCalendarProvider.fetch_upcoming(from_date, to_date)`
(`trading_system/calendar_provider.py`) returns `CalendarCandidate` values -
never a provider-specific shape - so the data source can be swapped later
without touching `CalendarEventRepository` or the API layer.
`FinnhubEarningsCalendarProvider` is the first adapter; `FINNHUB_API_KEY` is
backend-only and is never shipped to the Expo app.

## Occurrence identity

Identity for idempotent sync is `(instrument, event_type, source,
occurrence_key)` - deliberately excluding `scheduled_date`, since a provider
is free to move a still-`candidate` event's date on a later sync.
`occurrence_key` is what keeps recurring releases of the same company from
colliding: for Finnhub earnings it is derived from the row's `year`/
`quarter` fields (e.g. `"2026Q3"` vs. `"2026Q4"`), so a Q3 release moving its
date still updates the same candidate, while Q4 - once it appears - is a
genuinely new row, even if Q3 is already `tracked`. If Finnhub ever omits
`year`/`quarter`, `FinnhubEarningsCalendarProvider` falls back to keying on
the date itself (a documented limitation of that fallback-only path: a later
date revision would then look like a new occurrence). Manual entries have no
fiscal quarter by default and fall back to a fixed `"manual"` key per
`(instrument, event_type, source)` unless the caller supplies an explicit
`occurrence_key` (e.g. for a manually-tracked recurring report).

## Idempotent sync

`CalendarEventRepository.sync_candidates()` upserts by the occurrence
identity above. Once a row is `tracked` (or any later stage), a sync can
never silently overwrite its date, company name, or market, and can never
move its status back to `candidate`. In Supabase this is enforced atomically
by `upsert_calendar_candidate()` (see
`supabase/migrations/20260824090000_calendar_watchlist_events.sql`), not by
an application-level check-then-act:

- **First insert** is a plain `insert ... on conflict (instrument,
  event_type, source, occurrence_key) do nothing`. This is what makes two
  concurrent syncs racing to insert the *same* brand-new occurrence
  race-safe: `select ... for update` cannot lock a row that doesn't exist
  yet, so without the `on conflict` clause the loser of such a race would
  hit a raw unique-violation instead of an idempotent result. Postgres
  itself serializes the two inserts against the same unique index entry;
  the loser's insert becomes a no-op and falls through to the same locked
  read below.
- **Once a row exists**, a `select ... for update` row lock plus a
  conditional `update` (only when `status = 'candidate'`) is what makes
  "already tracked -> never silently overwritten" atomic against a
  concurrent sync or `track()`/`untrack()` call.

## Range MVP: 7/30 days

This MVP deliberately only ever looks a short way ahead - candidate/tracked
data is not something that needs a long lookahead the way a full earnings
calendar product eventually might:

- Mobile date-range chips are `7 pv` / `30 pv` only (default `7 pv`, max
  `30 pv`) - no `90 pv`/`180 pv`/`Kaikki` option for the date filter (the
  market/country filter's own `Kaikki` option is unrelated - see "Mobile"
  below).
- `GET /api/v1/calendar/upcoming` defaults to, and rejects any request for,
  more than `MAX_CALENDAR_LOOKAHEAD_DAYS` (30, `trading_system/api.py`) -
  `422` if `to_date - from_date` exceeds it.
- `trading_system/calendar_sync_worker.py` fetches at most `MAX_LOOKAHEAD_DAYS`
  (30) - a larger `--lookahead-days`/`MARKETAI_CALENDAR_LOOKAHEAD_DAYS` is
  clamped, not honored, so a misconfigured environment can never make the
  worker populate rows further out than the API will ever serve.
- The worker ingests `INGESTION_LOOKAHEAD_PADDING_DAYS` (1) further than
  that - `host_today -> host_today + 31` for the default/max case - purely
  an internal ingestion-window detail to absorb device/host calendar-day
  skew (see below); the UI's chips and the API's own 30-day cap are both
  unaffected.

The mobile UI computes its own date-only window from the *device's* local
clock - `deviceLocalDateWindow()` in `upcoming.tsx`, built on the same
`dateOnlyOrdinal()`/`parseDateOnlyOrdinal()` helpers the range chips
already used - and sends it explicitly as `GET
/api/v1/calendar/upcoming?from_date=...&to_date=...`. The GET is
deliberately never called parameter-free: that would leave the window to
the *backend host's* own `date.today()`, which can disagree with the
device's own calendar day around midnight, especially across timezones.
Switching the `7 pv`/`30 pv` chip re-derives this window and re-fetches -
the API's own `MAX_CALENDAR_LOOKAHEAD_DAYS` cap is still enforced
independently on every request regardless of what the client sends, so a
client bug asking for a wider window is still rejected outright, not
silently trusted or clamped.

A device whose local calendar day is *ahead* of this host's can still
legitimately ask for a day the API would otherwise have never had data
for: its widest (`30 pv`) request is `device_today -> device_today + 30`,
which in host-local terms can be `host_today + 1 -> host_today + 31`. The
worker's own `INGESTION_LOOKAHEAD_PADDING_DAYS` pad (see "Range MVP"
above) is what keeps that day already ingested by the time such a request
arrives - the API's own cap still rejects any single request spanning more
than 30 days, so this only ever helps a legitimately-windowed request find
data that's already there, never widens what a client can ask for in one
call.

## API

- `GET /api/v1/calendar/upcoming` - read auth (`X-MarketAI-Key`, same
  credential as the rest of the read API). `from_date`/`to_date` are
  optional; see "Range MVP" above for the default/max window.
- `POST /api/v1/calendar/{id}/track` / `.../untrack` - write auth reuses the
  existing `X-MarketAI-Control-Key` (never the read key, and no new
  `EXPO_PUBLIC_*` secret was added).
- `POST /api/v1/calendar/manual` - backend/tooling-only manual event entry,
  guarded by `X-Admin-Token`; never called from the Expo app.

`SupabaseCalendarEventRepository.list_upcoming()` pages through a result
set wider than one Data API response (`_LIST_UPCOMING_PAGE_SIZE`, matching
PostgREST's default `db-max-rows`) using keyset (cursor) pagination on
`(scheduled_date, id)`, not offset-based `.range()`. Each page's `WHERE`
clause is anchored to the literal `(scheduled_date, id)` of the *previous*
page's actual last row - never a numeric offset, which a concurrent
insert/update elsewhere in the ordered set can silently shift, causing the
next fixed-offset page to either re-return an already-seen row or skip the
row that used to sit at that boundary. A page shorter than the page size
still ends the loop, so pagination always terminates deterministically
regardless of concurrent writes.

## Mobile

`mobile/src/app/events/upcoming.tsx` merges the real tracked
`EventExpectation` list with calendar candidates/tracked rows. An instrument
already tracked through `EventExpectation` (e.g. Hays) is filtered out of
the calendar candidates so it never renders as a duplicate untracked card
(`mergeUpcomingRows()`, which also sorts the merged rows upcoming-first -
see below). Each candidate card has a "Lisää seurantaan" action that calls
`trackCalendarEvent()`.

The market/country filter (`Kaikki` plus whatever markets are actually
present in `rows`) is derived entirely from the data, never a hardcoded
country list, and applies independently of the date-range filter - the two
combine as a plain AND in `filtered`'s predicate, neither one affecting
which options the other offers.

`mergeUpcomingRows()` sorts its combined output deterministically -
upcoming (today or later) first, soonest first; history after, most
recently released first - mirroring the backend's own `list_upcoming()`
ordering. Candidate/tracked/expectation origin never decides order, only
`scheduled_date` does.

The results themselves render through a `FlatList` (`data={filtered}`,
`renderItem`, `keyExtractor={(row) => row.key}`), not a `ScrollView`
eagerly mapping every row into a `View` - a candidate/tracked result set
can run into the hundreds, and only the cards actually near the viewport
are ever mounted. The search box, market/date filter chips, and the
loading/error/empty states move into `ListHeaderComponent`; pull-to-refresh
is unchanged, still `RefreshControl`-driven off the same `refreshing`/
`onRefresh` state.

A track mutation always wins over an older, still-in-flight refresh GET -
but only for the one row it actually touched. `onTrack()` records a
row-scoped override (`localCalendarOverrides`, keyed by
`calendar_event_id`) tagged with a monotonically increasing
`mutationVersion`, rather than bumping the same generation counter
`load()` uses for its own overlap guard (`latestCalendarLoadId`/
`latestEventsLoadId` still exist, but only ever guard against two
*overlapping* `load()` calls on the same source - e.g. a quick double
pull-to-refresh, or a range-chip change firing a new request before the
previous one settled). Every calendar response, via
`applyLocalCalendarOverrides()`, re-applies any override whose version is
newer than the mutation-version snapshot taken when *that* response's own
request started - so a still-pending, genuinely newer response (e.g. a
wider-range 30 pv request in flight when a 7 pv candidate gets tracked) is
never discarded wholesale just to protect one row.

A failed calendar GET is never flattened into an empty result. `load()`
keeps `calendarEvents` and `calendarError` in two entirely separate
pieces of state: success clears `calendarError` and replaces
`calendarEvents`; failure sets `calendarError` and leaves `calendarEvents`
completely untouched, so whatever was last successfully loaded (or
`null`, on a first-ever failed load) stays exactly as it was. The UI
renders a dedicated error/retry card (never the "Ei julkaisuja" empty
state, which is reserved for a genuine, successful empty result) and its
retry button just calls `load(rangeDays)` again. The `events`/
`EventExpectation` side has its own independent `error` state and keeps
working normally regardless of what the calendar side is doing.

## Production scheduling

`trading_system/calendar_sync_worker.py` runs one sync pass and exits - it
is not a long-running worker (deliberately: candidate/tracked rows aren't
time-sensitive the way live trading state is, so a scheduled one-shot job
is enough for this MVP). Production scheduling is
`deploy/systemd/marketai-calendar-sync.service` (`Type=oneshot`) plus
`deploy/systemd/marketai-calendar-sync.timer` (`OnCalendar=*-*-* 06,18:00:00`,
i.e. twice a day, with `RandomizedDelaySec` jitter and `Persistent=true` so
a run missed while the host was down still fires once after boot instead of
silently waiting for the next scheduled time).

`.github/workflows/deploy-seesam-hub.yml`'s "Deploy backend to seesam-hub
(locked)" step installs/updates both unit files, runs `systemctl
daemon-reload`, and `systemctl enable --now` the timer - after the Supabase
schema gate (`scripts/verify_supabase_schema.py`) and before the
`marketai-api.service`/`marketai-hays-release.service` restarts, so a
missing calendar migration stops this too, the same fail-closed story as
the rest of that step. `enable --now` only arms the *timer* (starts it
counting toward its next `OnCalendar` fire); it does not run the sync
immediately, so this is safe to re-run on every deploy even when nothing
about the timer changed.

**This needs one out-of-band host change, the same category as applying
Supabase migrations (see "Deploy gate: Supabase schema verification" in
`docs/event_configuration_storage.md`):** the deploy runner's sudoers entry
on seesam-hub must additionally permit, without a password, the exact
commands above -
`/usr/bin/install -m 0644 deploy/systemd/marketai-calendar-sync.service /etc/systemd/system/marketai-calendar-sync.service`,
the equivalent for `.timer`, `/usr/bin/systemctl daemon-reload`, and
`/usr/bin/systemctl enable --now marketai-calendar-sync.timer` - before
this deploy step can succeed on a real run. Grant that (`visudo` /
`/etc/sudoers.d/`) using your own access to the host; this repo and its CI
hold no credential that could do it.
