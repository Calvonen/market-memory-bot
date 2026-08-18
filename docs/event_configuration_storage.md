# Editable event configuration

Pre-event expectations are configuration/data, not trading-engine code.

## Production storage

The dedicated `MarketAI` Supabase project is now the source of truth for editable event setup. The existing EnergiaZen project is not used by this system.

Current schema:

- `public.market_events`: event identity, instrument, date and status
- `public.event_expectation_versions`: immutable versioned consensus, KPI, scenario and trigger snapshots
- `public.current_event_expectations`: security-invoker view exposing the latest version to the backend service role

Each edit inserts a new `event_expectation_versions` row. Database triggers reject UPDATE and DELETE against historical expectation versions.

Strategy decisions should reference the exact expectation version used at decision time. Editing a current event therefore never rewrites historical decision context.

## Editing flow

The Expo app can later expose an Events/Edit screen:

1. open an upcoming event
2. edit consensus, ranges, KPI importance, scenarios or triggers
3. save through the FastAPI backend
4. backend validates the payload and requires a `change_note`
5. backend inserts a new immutable Supabase expectation version
6. workers read the active version through `EventExpectationRepository`

The mobile client must never receive `MARKETAI_SUPABASE_SECRET_KEY`. FastAPI owns privileged Supabase access. The current MVP write endpoint additionally requires `X-Admin-Token`; this can later be replaced by Supabase Auth without changing the repository boundary.

## Backend environment

Required for Supabase-backed event reads/writes:

```text
MARKETAI_SUPABASE_URL=https://<project-ref>.supabase.co
MARKETAI_SUPABASE_SECRET_KEY=<backend-only secret/service-role key>
MARKETAI_ADMIN_API_KEY=<separate long random API admin token>
```

Run the first API locally with:

```text
uvicorn trading_system.api:app --reload
```

Current endpoints:

- `GET /health`
- `GET /api/v1/events`
- `GET /api/v1/events/{event_id}`
- `POST /api/v1/events/{event_id}/expectation-versions`

The POST endpoint creates a new version; it never edits the previous version in place.

## Safety boundary

Editable event values can influence Strategy Engine evidence but cannot:

- change Risk Engine hard limits
- enable live trading
- bypass kill switch
- call a broker directly
- rewrite historical strategy/risk decisions

Risk defaults and broker safety remain code/configuration controlled separately from event research data.

## Current Hays seed

`hays-fy2026-results` / `HAS.L` is seeded in MarketAI as expectation version 1 for 20 August 2026. The test fixture remains only as deterministic test data; production code reads current event configuration through the repository boundary.
