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
`source`, `status` - deliberately no time-of-day, release URL, consensus, or
KPI data at the candidate/tracked stage. `event_type` is a plain string, not
hardcoded to `"earnings"` - a manually-entered event (e.g. a production
report no calendar provider tracks) is exactly as valid a candidate.

## Provider interface

`EarningsCalendarProvider.fetch_upcoming(from_date, to_date)`
(`trading_system/calendar_provider.py`) returns `CalendarCandidate` values -
never a provider-specific shape - so the data source can be swapped later
without touching `CalendarEventRepository` or the API layer.
`FinnhubEarningsCalendarProvider` is the first adapter; `FINNHUB_API_KEY` is
backend-only and is never shipped to the Expo app.

## Idempotent sync

`CalendarEventRepository.sync_candidates()` upserts by `(instrument,
event_type, source)` - deliberately excluding `scheduled_date`, since a
provider is free to move a still-`candidate` event's date on a later sync.
Once a row is `tracked` (or any later stage), a sync can never silently
overwrite its date, company name, or market, and can never move its status
back to `candidate`. In Supabase this is enforced atomically by
`upsert_calendar_candidate()` (a `select ... for update` + conditional
`update`, see `supabase/migrations/20260824090000_calendar_watchlist_events.sql`),
not by an application-level check-then-act.

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
