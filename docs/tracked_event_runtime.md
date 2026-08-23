# Persistent tracked-event runtime

This runtime turns an explicitly tracked scheduled event into persistent market-reaction monitoring. It does **not** create a strategy decision, trading task, broker order, or trade.

## Separation of responsibilities

- `calendar_events`: discovery/watchlist state (`candidate` / `tracked` and later research lifecycle).
- `tracked_market_events`: one concrete scheduled event that MarketAI should monitor at an exact timezone-aware timestamp.
- `tracked_market_event_reactions`: persistent closed-candle reactions produced by the worker.
- Trading tasks and broker execution remain separate. Tracking an event is never an instruction to trade.

## Canonical writer

Trusted agents/tooling create or update a runtime event through the Supabase RPC `upsert_tracked_market_event(...)`.

Identity is `(source, external_key)`. Repeating the same request is idempotent. The RPC rejects an attempt to reuse that identity for a different instrument/kind/calendar event. Once monitoring has started or a pre-event reference has been captured, material event timing/identity is locked; an agent must not silently retarget the running event.

All timestamps supplied by clients must be timezone-aware. The repository normalizes persisted event timestamps to UTC.

`event_time_status` is one of:

- `confirmed`
- `estimated`
- `unknown`

This is descriptive event metadata, not a trading threshold.

## Worker

`python -m trading_system.tracked_event_worker`

The systemd unit is `marketai-tracked-events.service`.

The worker:

1. Reads `tracked` / `monitoring` runtime events from Supabase.
2. Reconstructs the persistent tracked-instrument identity.
3. Resolves the instrument through the production eToro resolver; ambiguity fails closed.
4. Before `event_at`, captures eToro `lastExecution` as an explicit pre-event snapshot baseline and persists it exactly once.
5. Waits until `event_at`.
6. Opens the normal eToro live stream and continuous 1m -> 5m/15m candle pipeline.
7. Uses the existing reaction monitoring profile (1m, then 5m, then 15m) to select which complete closed candles drive reaction analysis.
8. Persists reaction, direction and evolution rows idempotently.
9. Marks the runtime event completed after its configured observation horizon.

There is no StrategyEngine, RiskEngine, Broker, PaperBroker, EtoroDemoBroker or real-money execution call in this worker.

## Overnight / closed-market events

A scheduled release may happen while its exchange is closed. In that case there may be no valid pre-event 1-minute candle in the live runtime at all. The worker therefore stores the eToro pre-event `lastExecution` snapshot as an explicit baseline (`reference_kind = etoro_last_execution_pre_event_snapshot`) instead of fabricating a candle.

For example, if an Australian earnings release occurs before the ASX opens, the pre-event snapshot can represent the previous session's last execution. The first reaction candle is still a real fully closed post-event candle from the normal live candle pipeline.

If the worker reaches `event_at` without a persisted pre-event baseline, it fails closed. It never fetches a post-event quote and backdates it as a reference.

## Configuration

Optional backend environment variables:

- `MARKETAI_TRACKED_EVENT_POLL_SECONDS` (default 30)
- `MARKETAI_TRACKED_EVENT_LOOKAHEAD_HOURS` (default 24)
- `MARKETAI_TRACKED_EVENT_MAX_PAST_HOURS` (default 12)
- `MARKETAI_TRACKED_EVENT_MONITOR_HOURS` (default 8)

These are runtime scheduling/retention settings, not trading strategy limits.

## Creating NHF FY26 as a test event

The canonical values for the planned test are represented as data, not code:

- instrument: `NHF.ASX`
- company: `nib holdings limited`
- kind: `earnings`
- source: `manual_ir`
- external key: `nhf-fy26-2026-08-24`
- event timestamp: timezone-aware exact/estimated timestamp supplied by the operator/agent
- event-time status: `confirmed` or `estimated` according to the source evidence

Once that row exists, the worker owns monitoring. No assistant needs to stay awake or keep a chat session running.
