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
- **persisted**: `POST /api/v1/events/{event_id}/strategy-draft/approve` re-validates the exact same draft, then checks three independent conditions before persisting anything:
  1. **Draft-fingerprint check** - the draft in the approval request must hash to the same `draft_fingerprint` the preview returned, so an approved payload can never silently differ from what was reviewed. `StrategyDraftApprovalRequest.draft_fingerprint` is validated as exactly 64 `[0-9a-f]` (lowercase only) characters (`Field(min_length=64, max_length=64, pattern=...)`) before this check ever runs - `secrets.compare_digest()` raises `TypeError` on a non-ASCII string argument, so a malformed fingerprint used to be able to crash the request as an unhandled 500 instead of the 422 a bad request should produce. The field's case policy is unambiguous: a `field_validator(mode="before")` lowercases the submitted value first, so a semantically identical uppercase or mixed-case fingerprint (e.g. copy-pasted from somewhere that renders hex uppercase) is accepted and compared/audited the same as its lowercase form - it is never rejected with a false 409 just because of letter case. `draft_fingerprint()` itself only ever produces lowercase hex, so the canonical form recorded in the response and the `event_strategy_approvals` audit row is always lowercase regardless of how the caller submitted it.
  2. **Identity check** - the `{event_id}` in the URL is authoritative. If the draft's `instrument`, `event_name` or `scheduled_date` don't match the event being approved against, approval hard-fails (409) - unlike preview, where the same mismatch is only a warning (`identity_mismatches()` in `trading_system/strategy_draft.py` backs both checks).
  3. **Expectation-version CAS** - the event's current version must still equal the `base_expectation_version` the approval was based on.

  The CAS check, the new expectation-version insert, and the audit-trail insert are **one atomic database operation** (`StrategyDraftApprovalRepository.approve()`, backed in production by the `approve_strategy_draft()` Postgres function - see `supabase/migrations/20260821090000_event_strategy_approvals.sql`), never `EventExpectationRepository.save()`'s own `max(version)+1`-with-retry loop. That loop has no way to enforce "the version I previewed against is still current" - a read-then-write CAS check in Python around it left a real race where two concurrent approvals against the same base version could both pass the check before either wrote, producing two new versions instead of one succeeding and one conflicting. The Postgres function closes this with `pg_advisory_xact_lock` (the same per-event-serialization pattern the paper-run RPCs already use): concurrent approvals for the same event serialize through the function one at a time, so a loser's version check runs against the winner's already-committed version and correctly 409s - and a retried/duplicated approve request fails the same way, rather than creating a duplicate version. The version-insert and the audit-insert happen in the same transaction, so a failure in either one leaves neither committed.

  `StrategyDraftPayload.summary`/`change_note` are stripped *before* their `min_length` check runs (a `field_validator(mode="before")` in `trading_system/strategy_draft.py`), not only afterwards in `normalize_draft()` - a whitespace-only value can never pass validation and later collapse to empty, on either preview or approve. `StrategyDraftApprovalRequest.approved_by` (`trading_system/api.py`) gets the same treatment: a whitespace-only approver identity is rejected (422) and can never reach the persisted expectation version or the `event_strategy_approvals` audit row.

  Approval requires the `X-MarketAI-Control-Key` header (`MARKETAI_CONTROL_API_KEY`) - a **third**, independent credential from both the read key and the admin token (see Backend environment below). Every successful approval is recorded in `public.event_strategy_approvals` (who/what approved, when, the draft fingerprint, and the base expectation version) as an audit trail alongside the new `event_expectation_versions` row, inserted by the same atomic operation.

Only the persisted, approved version is ever visible through `EventExpectationRepository.get()` / `list_upcoming()` - the same read path the release worker and paper-trading pipeline use - so a draft or preview can never leak into a trading decision.

### Every `event_expectation_versions` writer shares one lock

The direct admin write endpoint (`POST .../expectation-versions`) and the strategy-draft approval endpoint both allocate the next `event_expectation_versions` row for an event. They now both take the *same* `pg_advisory_xact_lock(hashtextextended(event_id, 1))` before doing so - the admin path through a new `insert_next_expectation_version()` Postgres function (`supabase/migrations/20260821140000_shared_expectation_version_lock.sql`), called from `SupabaseEventExpectationRepository.save()` instead of that method's old, unlocked "select max(version), insert, retry on 23505" loop. A concurrent admin write and approval for the same event can therefore never race each other's version allocation: whichever acquires the lock first fully commits before the other's read-then-insert proceeds. The admin path has no caller-supplied expected version to check (it has never offered CAS semantics - it just writes on top of whatever is current), so it always succeeds once it holds the lock; only the approval path can conflict, and it does so as a controlled `ExpectationVersionConflict` -> 409, never an unmapped 500 (`SupabaseStrategyDraftApprovalRepository._is_version_conflict()` also treats a raw `23505` as this same conflict, as defense in depth, in case some future writer is ever added without taking the lock). The in-memory test doubles mirror this by sharing one `threading.Lock` (`InMemoryEventExpectationRepository.lock`, reused by `InMemoryStrategyDraftApprovalRepository`).

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

## Deploy gate: Supabase schema verification

The strategy-draft approval flow's backend code calls Supabase objects - `public.event_strategy_approvals`, `public.approve_strategy_draft()`, `public.insert_next_expectation_version()` - that exist only once their migrations (`supabase/migrations/20260821090000_event_strategy_approvals.sql`, `20260821140000_shared_expectation_version_lock.sql`, `20260822090000_verify_strategy_draft_schema.sql`) have been applied to the target Supabase project.

**Migrations are applied out-of-band, not by CI.** `.github/workflows/deploy-seesam-hub.yml`'s self-hosted deploy job holds no Postgres-DDL-capable credential and is never given one - it only has the same `MARKETAI_SUPABASE_URL`/`MARKETAI_SUPABASE_SECRET_KEY` the running backend service itself uses (via the Supabase Data API/PostgREST, which cannot execute arbitrary DDL). Before merging a commit that depends on new Supabase schema, apply the corresponding migration(s) to the target project yourself (`supabase db push`, or the SQL editor in the Supabase dashboard) using your own Supabase CLI/dashboard credentials - never a secret added to this repo, to CI, or to the mobile bundle.

**The deploy workflow still verifies this deterministically, every time - it does not rely on remembering to check manually.** The "Deploy backend to seesam-hub (locked)" step now runs `scripts/verify_supabase_schema.py` after fast-forwarding the checkout but *before* either `systemctl restart` line. That script calls the read-only `verify_strategy_draft_schema()` RPC (added by the third migration above), which checks `to_regclass()`/`to_regprocedure()` catalog existence for all three required objects - no data is read or written, and calling it repeatedly is always safe. If the RPC call fails outright (almost always meaning the migrations haven't been applied yet - including the case where the verify RPC itself is missing) or any individual check comes back false, the script exits non-zero.

GitHub Actions `run:` steps use `bash -eo pipefail` by default, so that non-zero exit stops the step immediately: neither `systemctl restart marketai-api.service` nor `systemctl restart marketai-hays-release.service` runs, and the currently-running (older) backend keeps serving traffic untouched - **fail closed**, never a bad restart. This applies to *every* push to `feature/trading-system-foundation`, not just this PR's schema - any future PR that adds a new Supabase dependency should extend `verify_strategy_draft_schema()` (or add a similarly-shaped RPC) and `scripts/verify_supabase_schema.py`'s `REQUIRED_CHECKS` the same way.

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

The draft/preview/approval-confirmation state lives in `strategy/_layout.tsx`'s React Context, keyed on nothing but component lifetime by default - so `mobile/src/utils/use-reset-on-key-change.ts` (`useResetOnKeyChange`) resets it, and each screen's own local state, the moment the route's `eventId` param changes. This runs as a guarded conditional state update during render (React's documented "adjusting state when a prop changes" pattern), not inside an effect, so there is no frame where a previous event's draft, preview, or an already-toggled approval acknowledgement can render or be actionable under a different event's route.

`strategy/index.tsx`'s own staleness guard (`latestLoadId`) is also bumped inside that same route-change reset, not only from inside `load()` itself. `load()` is invoked from a deferred `setTimeout(0)`, so without this, a slow response for the *previous* event could still resolve in the gap between the route changing and the new `load()` actually starting, find the ref not yet bumped, pass the staleness check, and repopulate the just-cleared screen with the wrong event's data.

`onApprovePress`'s `previewStrategyDraft()` call carries the same kind of guard (`latestPreviewRequestId`), invalidated both by that same route-change reset and by the screen unmounting (a `useEffect` cleanup). A stale preview response - resolved after the user already changed events or navigated away - can therefore never call `setPreview()` or push the confirmation screen for the wrong event.

`strategy/confirm.tsx`'s `submitApproval()` carries the identical guard (`latestApprovalRequestId`), invalidated by its own route-change reset and unmount cleanup. If event A's approval is still in flight when the route moves to event B, A's eventual response can neither clear B's draft/preview nor navigate away from B nor write B's error/conflict/submitting state - the success, catch, and finally branches are each guarded independently.

`mobile/src/utils/strategy-draft-format.ts` edits consensus/triggers as a single JSON object literal, not "key: value" lines - a line-based, colon-split format can never be genuinely lossless for arbitrary keys, since nothing stops a real metric/trigger name from containing a colon itself (e.g. `"Revenue: FY27"`), which a naive `line.indexOf(':')` split can't tell apart from the key/value separator. `recordToText()` renders a record via `JSON.stringify(record, null, 2)`; `parseTypedRecord()` parses the whole text back via `JSON.parse`, validating the result is a plain object whose values are all `string | number | null` - never guessing or normalizing. This is what makes an arbitrary key (including one containing `:`) round-trip byte-for-byte, the string `"001"` stay a string, the string `"null"` stay a string, real `null` stay `null`, and a real number stay a number. Invalid input (malformed JSON, a non-object top level, or a disallowed value type like a boolean or nested object) is never silently coerced: `parseTypedRecord()` returns `{ ok: false, error }`, shown directly to the user (`strategy/edit.tsx`'s inline field error, and `strategy/index.tsx`'s `RecordCard` on the summary screen), and `textToTypedRecord()` (used by `draftFormToInput()`, which feeds preview/approve) throws for the same input instead of sending something "close enough" to the backend.

`parseTypedRecord()` builds its result object via `Object.fromEntries(entries)` from a plain array of `[key, value]` pairs, never by assigning into a `{}` literal with `result[key] = value`. That distinction matters for a key literally named `"__proto__"`: JSON itself treats it as an ordinary string key (`JSON.parse` creates a real own property for it, same as any other key), but bracket-assigning into a plain object with that exact key name invokes `Object.prototype`'s special `__proto__` *accessor setter* instead of creating a data property - which silently discards the value entirely when it isn't an object (a string, number, or `null`, exactly the types this parser accepts). `Object.fromEntries` uses `CreateDataProperty` semantics and has no such special case, so a `"__proto__"` key round-trips exactly like every other key instead of vanishing without an error.

Saving an edited draft (`strategy/edit.tsx`'s `save()`) always clears the shared `preview` from context (`setPreview(null)`) alongside committing the edited fields (`setDraft(local)`) - unconditionally, on every save, not only when the edited text obviously differs from before. Without this, the summary screen (`strategy/index.tsx`) could keep showing the stale `ESIKATSELTU` state and stale preview warnings for a draft that has since changed underneath them, and the confirm screen could still be reachable with an approval based on a preview that no longer matches the current draft. Clearing the preview on every save forces a fresh `previewStrategyDraft()` call before approval can proceed again.
