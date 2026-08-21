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

The mobile client must never receive `MARKETAI_SUPABASE_SECRET_KEY` or `MARKETAI_ADMIN_API_KEY`. FastAPI owns privileged Supabase access. The current MVP write endpoint requires `X-Admin-Token`; this can later be replaced by Supabase Auth without changing the repository boundary.

## Read authentication (mobile app)

`GET /api/v1/events`, `GET /api/v1/events/{event_id}` and `GET /api/v1/events/{event_id}/paper-status` return strategy-relevant data (consensus, bull/base/bear cases, trigger thresholds, risk/paper-trade state) and require a separate, lower-privilege `X-MarketAI-Key` header, checked against `MARKETAI_READ_API_KEY`. This key is intentionally **not** the admin token: it cannot create expectation versions. If `MARKETAI_READ_API_KEY` is not configured, these endpoints fail closed (503) rather than being open. `GET /health` stays unauthenticated and returns no strategy data.

**This is an MVP-only credential, not real user authentication.** A `EXPO_PUBLIC_*` env var is compiled into the client bundle and is readable by anyone who has the app (APK/IPA) or inspects app traffic - it is not a secret once shipped, regardless of how it is obtained. Treat a shipped read key as effectively public: acceptable only while distribution is trusted/private (e.g. TestFlight/internal, or the API itself is unreachable from the public internet - see below). Before any public app-store release or public API exposure, replace this with per-user authentication (e.g. Supabase Auth / a real login), scoped API tokens issued per device, and server-side rate limiting - not a longer-lived shared secret shipped in the bundle.

## Backend environment

Required for Supabase-backed event reads/writes:

```text
MARKETAI_SUPABASE_URL=https://<project-ref>.supabase.co
MARKETAI_SUPABASE_SECRET_KEY=<backend-only secret/service-role key>
MARKETAI_ADMIN_API_KEY=<separate long random API admin token>
MARKETAI_READ_API_KEY=<separate long random read-only token for GET endpoints>
```

Run the first API locally with:

```text
uvicorn trading_system.api:app --reload
```

Current endpoints:

- `GET /health` (no auth)
- `GET /api/v1/events` (requires `X-MarketAI-Key`)
- `GET /api/v1/events/{event_id}` (requires `X-MarketAI-Key`)
- `GET /api/v1/events/{event_id}/paper-status` (requires `X-MarketAI-Key`)
- `POST /api/v1/events/{event_id}/expectation-versions` (requires `X-Admin-Token`)

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

## Mobile: generic event tracking UI

The Expo app home screen (`mobile/src/app/(tabs)/index.tsx`) now lists every event returned by `GET /api/v1/events` as a compact "Seurannassa" card, instead of hardcoding the Hays event. Tapping a card opens `mobile/src/app/events/[eventId].tsx`, a generic detail screen driven entirely by the route's `eventId` param. It shows the pre-release expectation (consensus, KPIs, bull/base/bear cases, triggers, invalidation conditions, source) always, and layers the paper-trade dashboard (fundamental/catalyst/technical/market-memory scores, strategy direction/confidence, risk, paper order, confirmation reason) on top once a paper run exists for that event. `mobile/src/app/events/upcoming.tsx` is a foundation page for a future earnings-calendar provider: it only ever renders real tracked events (never mocked/fabricated data) with working date/market/ticker filters, ready for a calendar source to plug untracked releases into the same filter UI and "Lisää seurantaan" action in a later PR.

`mobile/src/app/events/[eventId]/edit.tsx` is a UI-only settings/editor draft for consensus, KPIs, bull/base/bear cases, triggers, invalidation conditions and source. It intentionally never calls the admin-protected write endpoint and never receives `MARKETAI_ADMIN_API_KEY` - the Save action is disabled with an explanatory note. **Next PR TODO:** replace the disabled Save action with a real write path once a secure mobile-control-auth mechanism exists (e.g. per-device scoped tokens issued through Supabase Auth, not the shared read key). Until then, editing expectations still requires calling the admin API directly (e.g. via `curl`/an internal tool), not the mobile app.
