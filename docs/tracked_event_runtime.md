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

`event_time_status` is one of `confirmed`, `estimated`, or `unknown`. This is descriptive event metadata, not a trading threshold.

## eToro resolution preflight

Broker discovery is deliberately separated from the timing-critical event path.

When an unarmed tracked event enters the worker lookahead window, the worker starts a background preflight. Preflight:

1. resolves the tracked identity through the production eToro resolver;
2. fetches a quote for the resolved instrument id to verify that the market-data account can use it; and
3. persists the exact eToro instrument id, symbol and display name through `arm_tracked_market_event_resolution(...)`.

Only one catalog traversal is started at a time. Slow catalog discovery therefore does not block the asyncio event loop or an already-armed event monitor.

`monitor_one_event()` never performs catalog discovery. It requires a complete persisted eToro identity and fails closed if the event is not armed. Reference capture and WebSocket subscription reuse the persisted instrument id. The reference RPC also verifies that the supplied identity is exactly the already-armed identity and cannot silently retarget the event.

## Worker

`python -m trading_system.tracked_event_worker`

The systemd unit is `marketai-tracked-events.service`.

The worker:

1. reads `tracked` / `monitoring` runtime events from Supabase;
2. preflights and persists unresolved eToro identity ahead of the event;
3. reconstructs the persisted armed eToro identity without a catalog search in the event monitor;
4. before `event_at`, captures eToro `lastExecution` as an explicit pre-event snapshot baseline and persists it exactly once;
5. waits until `event_at`;
6. opens the normal eToro live stream and continuous 1m -> 5m/15m candle pipeline;
7. anchors reaction-stage timing to the first real complete post-event 1m candle;
8. uses the existing reaction monitoring profile (1m, then 5m, then 15m);
9. persists reaction, direction and evolution rows idempotently and restores that evolution state after restart; and
10. marks the runtime event completed after its configured observation horizon.

There is no StrategyEngine, RiskEngine, Broker, PaperBroker, EtoroDemoBroker or real-money execution call in this worker.

## Overnight / closed-market events

A scheduled release may happen while its exchange is closed. In that case there may be no valid pre-event 1-minute candle in the live runtime at all. The worker therefore stores the eToro pre-event `lastExecution` snapshot as an explicit baseline (`reference_kind = etoro_last_execution_pre_event_snapshot`) instead of fabricating a candle.

For example, if an Australian earnings release occurs before the ASX opens, the pre-event snapshot can represent the previous session's last execution. The first reaction candle is still a real fully closed post-event candle from the normal live candle pipeline. Reaction-stage timing starts from that first real candle rather than consuming the 1m stage while the exchange is closed.

If the worker reaches `event_at` without both an armed eToro identity and a persisted pre-event baseline, it fails closed. It never fetches a post-event quote and backdates it as a reference.

## Configuration

Optional backend environment variables:

- `MARKETAI_TRACKED_EVENT_POLL_SECONDS` (default 30)
- `MARKETAI_TRACKED_EVENT_LOOKAHEAD_HOURS` (default 24)
- `MARKETAI_TRACKED_EVENT_MAX_PAST_HOURS` (default 12)
- `MARKETAI_TRACKED_EVENT_MONITOR_HOURS` (default 8)
- `MARKETAI_TRACKED_EVENT_REFERENCE_LEAD_SECONDS` (default 30)
- `MARKETAI_TRACKED_EVENT_MAX_WAIT_FOR_MARKET_HOURS` (default 72)

These are runtime scheduling/retention settings, not trading strategy limits.

## NHF FY26 test event

The test event is represented as data, not code:

- instrument: `NHF.ASX`
- company: `nib holdings limited`
- kind: `earnings`
- source: `manual_ir`
- external key: `nhf-fy26-2026-08-24`
- event timestamp: timezone-aware estimated timestamp

Once the row exists and the worker has armed its eToro identity, MarketAI owns the monitoring. No assistant or chat session needs to remain active.