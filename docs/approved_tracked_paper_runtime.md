# Approved tracked PAPER runtime

`trading_system.approved_tracked_paper_worker` is the production entry point for already-approved canonical PAPER trading tasks. It discovers `trading_tasks` rows where `state = 'approved'` and `mode = 'PAPER'`, then calls `run_approved_tracked_paper_once(...)` for the exact task/event identity.

The worker does not create or approve tasks and does not enable LIVE execution. Broker selection is explicit: production demo execution requires `MARKETAI_PAPER_BROKER=etoro_demo`; the safe default remains `internal`. Regardless of broker, the canonical task, claim, Strategy/Risk audit, portfolio lease, and durable broker-attempt idempotency controls remain mandatory.

## PAPER trading readiness checklist

A tracked event, release analysis, or approved task alone is **not enough** to make a PAPER trade possible. Before expecting an event to execute, verify the complete chain below.

1. **Event and analysis are ready.** The tracked event exists, has the canonical source-event identity, a current expectation version, a persisted release analysis for that version, a valid eToro instrument mapping, a captured reference price, and market-reaction observations are arriving without `last_error`.
2. **Execution authority exists.** There is exactly one active `trading_tasks` row for the event with `mode = 'PAPER'` and `state = 'approved'`. Its `approved_expectation_version` must equal the current expectation version. When the capped permission flow is deployed, the approved `max_position_value_usd` must also match the amount the operator confirmed in the app.
3. **Production runtime is deployed first.** `/home/marko/marketai-deploy-state/last-deployed-backend.sha`, `/home/marko/marketai-repo` HEAD, and the intended `feature/trading-system-foundation` deployment SHA must refer to the same production revision.
4. **Run readiness for that exact SHA.** In GitHub Actions run **Prepare approved PAPER worker** from `feature/trading-system-foundation`. It must finish successfully. This step validates the schema, canonical eToro identity, broker configuration and Virtual Portfolio access; installs the systemd unit; and records `approved-paper-prepared.sha`, `approved-paper-prepared.env`, and its digest. Never create these readiness files manually.
5. **Use demo broker configuration intentionally.** The prepared environment must contain `MARKETAI_PAPER_BROKER=etoro_demo`, valid `ETORO_API_KEY` and `ETORO_USER_KEY`, a finite positive `MARKETAI_ETORO_DEMO_MAX_AMOUNT_USD`, and a valid `MARKETAI_PAPER_SPREAD_PCT`. Do not print secret values while checking configuration.
6. **Start through the guarded script.** Run:

   ```bash
   cd /home/marko/marketai-repo
   bash scripts/start_approved_paper_worker.sh
   ```

   Do not bypass this with a raw `systemctl start`. The script refuses to start if deployed SHA, prepared SHA, runtime checkout, environment digest, systemd unit, or readiness state differ.
7. **Verify the worker really became active.** The start script must report `marketai-approved-paper.service is active ...`. In the database, an active worker that is processing approved work should be able to acquire the PAPER portfolio execution lease. Absence of `event_paper_trade_runs` or `event_paper_broker_attempts` is not by itself a failure: Strategy, market confirmation, Market Memory, Technical, Risk Engine, and other fail-closed gates may still legitimately produce NO TRADE.
8. **Do not force execution after approval.** Approval grants authority; it does not order a trade. A broker attempt may be created only after the canonical confirmation → Strategy → Risk path authorizes it. An unresolved broker attempt or active execution lease must be reconciled before authority is replaced or cancelled.

Operational rule: **after every backend deployment that changes the production SHA, treat approved PAPER readiness as stale until the readiness workflow has succeeded for the new SHA and the guarded start script has activated the worker again.** This prevents the common failure mode where the app shows an approved event but no execution worker is actually eligible to process it.

## Required PAPER portfolio inputs

For the internal paper broker path, the service fails closed at startup unless these values exist in the prepared environment snapshot:

- `MARKETAI_PAPER_EQUITY`
- `MARKETAI_PAPER_CASH`
- `MARKETAI_PAPER_OPEN_POSITIONS`
- `MARKETAI_PAPER_SPREAD_PCT`

Optional values are `MARKETAI_PAPER_INSTRUMENT_EXPOSURE_PCT` and `MARKETAI_PAPER_DAILY_PNL`, both defaulting to zero. Volatility is deliberately not configured here: the reviewed post-release execution path derives it from the same market frame used for confirmation and levels when it is absent.

When `MARKETAI_PAPER_BROKER=etoro_demo`, the eToro Virtual Portfolio is the authoritative portfolio state used by the Risk Engine; readiness validates that state before the worker can be prepared.

Worker tuning variables are `MARKETAI_APPROVED_PAPER_POLL_SECONDS` (default 30), `MARKETAI_APPROVED_PAPER_LEASE_SECONDS` (default 120), and `MARKETAI_APPROVED_PAPER_BATCH_SIZE` (default 50).

## systemd

The repository ships `deploy/systemd/marketai-approved-paper.service`. Enabling or starting this service is an explicit operational action; merely tracking an instrument or event never starts execution authority.

The canonical manual start command is:

```bash
cd /home/marko/marketai-repo
bash scripts/start_approved_paper_worker.sh
```

Do not use a raw `systemctl start marketai-approved-paper.service` for operator activation. The start script serializes against deployment/readiness controls and verifies the exact prepared SHA, environment snapshot/digest, runtime checkout, branch, systemd fragment, and absence of unexpected drop-ins before starting the service.

## Crash and retry semantics

Before any broker call, MarketAI persists one durable broker attempt for the canonical event/task and stores the exact Strategy and Risk payloads that authorized the call. A completed broker attempt is reconciled into `event_paper_trade_runs` before Strategy/Risk can be recomputed on restart. A nonterminal/uncertain attempt is never blindly resubmitted.
