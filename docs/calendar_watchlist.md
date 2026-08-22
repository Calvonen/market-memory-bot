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

## API

- `GET /api/v1/calendar/upcoming` - read auth (`X-MarketAI-Key`, same
  credential as the rest of the read API).
- `POST /api/v1/calendar/{id}/track` / `.../untrack` - write auth reuses the
  existing `X-MarketAI-Control-Key` (never the read key, and no new
  `EXPO_PUBLIC_*` secret was added).
- `POST /api/v1/calendar/manual` - backend/tooling-only manual event entry,
  guarded by `X-Admin-Token`; never called from the Expo app.

## Mobile

`mobile/src/app/events/upcoming.tsx` merges the real tracked
`EventExpectation` list with calendar candidates/tracked rows. An instrument
already tracked through `EventExpectation` (e.g. Hays) is filtered out of
the calendar candidates so it never renders as a duplicate untracked card
(`mergeUpcomingRows()`). Each candidate card has a "Lisää seurantaan" action
that calls `trackCalendarEvent()`.

A track mutation always wins over an older, still-in-flight refresh GET:
`onTrack()` bumps the same `latestLoadId` generation counter `load()` uses
for its own staleness guard, right after applying the tracked result. Any
`getUpcomingCalendarEvents()` response that was already in flight before
the track completed - and therefore reflects a pre-track snapshot - carries
the now-stale `loadId` it captured at request time, so `load()`'s existing
`if (loadId !== latestLoadId.current) return;` guard discards it instead of
reverting the row back to `candidate`. A refresh started *after* the track
captures a fresh `loadId` and is unaffected.

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
