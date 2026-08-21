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

The FastAPI backend exposes a direct admin write endpoint:

1. edit consensus, ranges, KPI importance, scenarios or triggers
2. save through the FastAPI backend
3. backend validates the payload and requires a `change_note`
4. backend inserts a new immutable Supabase expectation version
5. workers read the active version through `EventExpectationRepository`

The mobile client must never receive `MARKETAI_SUPABASE_SECRET_KEY` or `MARKETAI_ADMIN_API_KEY`. FastAPI owns privileged Supabase access. This direct write endpoint requires `X-Admin-Token` and stays backend/tooling-only (e.g. `curl`, an internal script) - it is never called from the Expo app. The strategy-draft flow below is what the mobile app and any external assistant integration use instead.

## Strategy draft: draft -> preview -> approve -> persisted

A human should never have to hand-fill technical fields like `fy27_operating_profit_pre_exceptional_gbp_m` directly. Instead, a strategy for an event is put together as a **draft** (by hand, in conversation, or by an external assistant), shown back as a clear human-readable summary, and only written to Supabase after **explicit approval**. A draft never influences trading by itself.

State model:

```
draft -> preview -> approved -> persisted
```

- **draft**: a `StrategyDraftPayload` (`trading_system/strategy_draft.py`) - the same fields as `EventExpectation`, plus human-facing `summary`, `assumptions` and `unresolved_questions`. It exists only in the request body / the mobile app's local state; nothing is written anywhere yet, and it cannot affect the release worker or paper trading.
- **preview**: `POST /api/v1/events/{event_id}/strategy-draft/preview` normalizes and validates the draft, diffs it against the current expectation version, and returns warnings about likely mistakes (KPIs/triggers that don't match `consensus`, empty scenarios, missing NO TRADE/invalidation conditions, missing source, instrument/date drift). It requires only the read-tier `X-MarketAI-Key` and **never** writes to Supabase, triggers the worker, or creates a paper trade. The response includes a `draft_fingerprint` (a SHA-256 of the normalized draft) and the `base_expectation_version` it was compared against.
- **approved**: a human (in the mobile app) or a trusted external assistant reviews the preview and explicitly approves it.
- **persisted**: `POST /api/v1/events/{event_id}/strategy-draft/approve` re-validates the exact same draft, checks two independent optimistic-concurrency conditions, and only then calls `EventExpectationRepository.save()` to insert the next immutable expectation version:
  1. **Draft-fingerprint check** - the draft in the approval request must hash to the same `draft_fingerprint` the preview returned, so an approved payload can never silently differ from what was reviewed.
  2. **Expectation-version CAS** - the event's current version must still equal the `base_expectation_version` the approval was based on. Since a successful approve always advances the version, a retried/duplicated approve request automatically fails this check on its second attempt instead of creating a duplicate version.

  Approval requires the `X-MarketAI-Control-Key` header (`MARKETAI_CONTROL_API_KEY`) - a **third**, independent credential from both the read key and the admin token (see Backend environment below). Every successful approval is recorded in `public.event_strategy_approvals` (who/what approved, when, the draft fingerprint, and the base expectation version) as an audit trail alongside the new `event_expectation_versions` row.

Only the persisted, approved version is ever visible through `EventExpectationRepository.get()` / `list_upcoming()` - the same read path the release worker and paper-trading pipeline use - so a draft or preview can never leak into a trading decision.

### Provider-agnostic control API

The preview/approve endpoints, together with the existing read endpoints (`GET /api/v1/events/{event_id}`), form a small control API that any trusted external assistant integration can drive without any direct coupling to a specific LLM provider:

1. `GET /api/v1/events/{event_id}` (read key) - read the event's current state
2. `POST .../strategy-draft/preview` (read key) - submit a draft, get a validated preview back
3. a human reviews the preview and approves
4. `POST .../strategy-draft/approve` (control key) - submit the same draft plus the preview's fingerprint/version to persist it

This PR does not wire up any specific provider (OpenAI, Claude, etc.) - it only defines the HTTP contract those integrations would call.

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
MARKETAI_CONTROL_API_KEY=<separate long random token for strategy-draft approval only>
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
- `POST /api/v1/events/{event_id}/expectation-versions` (requires `X-Admin-Token`) - direct backend/tooling write, never called from the mobile app
- `POST /api/v1/events/{event_id}/strategy-draft/preview` (requires `X-MarketAI-Key`) - validates/normalizes a draft, never writes
- `POST /api/v1/events/{event_id}/strategy-draft/approve` (requires `X-MarketAI-Control-Key`) - persists a previewed, approved draft as the next expectation version

Every write endpoint creates a new version; none of them edit a previous version in place.

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

The primary way to change an event's strategy from the app is now `mobile/src/app/events/[eventId]/strategy/` (a small nested route group with its own `_layout.tsx` sharing draft/preview state via React Context):

- `strategy/index.tsx` - a clear summary screen: draft state, human-readable summary, consensus, KPIs, bull/base/bear cases, triggers, NO TRADE/invalidation conditions, sources, and any warnings/unresolved questions from the last preview. Exactly two actions: **Muokkaa luonnosta** (edit) and **Hyväksy strategia** (approve), matching the required UX.
- `strategy/edit.tsx` - a structured form that edits the local draft only; it never calls the backend.
- `strategy/confirm.tsx` - shown after a preview succeeds. States plainly that approving locks the event to expectation version N+1, shows the NO TRADE conditions/triggers and any warnings, and requires both an explicit "I've checked the limits" acknowledgement switch and a second native confirmation dialog before the approve request is sent - so a single ordinary tap can never approve anything.

`mobile/src/app/events/[eventId]/edit.tsx` (the original raw field editor) now serves only as an advanced/debug view of the current approved expectation's raw fields, reachable from the strategy summary screen. It still intentionally never calls the admin-protected write endpoint and never receives `MARKETAI_ADMIN_API_KEY`.

The strategy screens call `previewStrategyDraft()`/`approveStrategyDraft()` in `mobile/src/services/api.ts`. Preview uses the existing read key; approve uses a **new**, narrower-scoped `EXPO_PUBLIC_MARKETAI_CONTROL_API_KEY` that authorizes only the strategy-draft approve endpoint - never the read key (which must never write) and never the admin token or a Supabase service-role key (neither of which the mobile app ever holds). Like the read key, this is still an MVP-tier shared secret compiled into the client bundle; replace it with real per-user/per-device auth before any public release, same caveat as the read key above.
