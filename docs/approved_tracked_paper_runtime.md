# Approved tracked PAPER runtime

`trading_system.approved_tracked_paper_worker` is the production entry point for already-approved canonical PAPER trading tasks. It discovers `trading_tasks` rows where `state = 'approved'` and `mode = 'PAPER'`, then calls `run_approved_tracked_paper_once(...)` for the exact task/event identity.

The worker does not create or approve tasks and does not enable LIVE execution. In this PR the orchestration still uses the internal `PaperBroker` unless an explicit pipeline is injected by a caller. The later eToro Virtual Portfolio step must replace only that broker boundary and preserve the same canonical task, claim, decision-audit, and broker-attempt idempotency controls.

## Required PAPER portfolio inputs

The service fails closed at startup unless these values exist in `/home/marko/marketai/.env`:

- `MARKETAI_PAPER_EQUITY`
- `MARKETAI_PAPER_CASH`
- `MARKETAI_PAPER_OPEN_POSITIONS`
- `MARKETAI_PAPER_SPREAD_PCT`

Optional values are `MARKETAI_PAPER_INSTRUMENT_EXPOSURE_PCT` and `MARKETAI_PAPER_DAILY_PNL`, both defaulting to zero. Volatility is deliberately not configured here: the reviewed post-release execution path derives it from the same market frame used for confirmation and levels when it is absent.

Worker tuning variables are `MARKETAI_APPROVED_PAPER_POLL_SECONDS` (default 30), `MARKETAI_APPROVED_PAPER_LEASE_SECONDS` (default 120), and `MARKETAI_APPROVED_PAPER_BATCH_SIZE` (default 50).

## systemd

The repository ships `deploy/systemd/marketai-approved-paper.service`. Deploy it with the same repository/env-file convention as the tracked-event worker. Enabling this service is an explicit operational action; merely tracking an instrument or event never starts execution authority.

The canonical manual start command is:

```bash
cd /home/marko/marketai-repo
bash scripts/start_approved_paper_worker.sh
```

Do not use a raw `systemctl start marketai-approved-paper.service` for operator activation. The start script holds `/tmp/marketai-approved-paper-control.lock` until systemd has completed the start. The readiness workflow holds the same lock across its final inactivity check and every systemd unit mutation, so the documented operator start and readiness installation cannot race through the remaining check/mutate window.

## Crash and retry semantics

Before any broker call, MarketAI persists one durable broker attempt for the canonical event/task and stores the exact Strategy and Risk payloads that authorized the call. A completed broker attempt is reconciled into `event_paper_trade_runs` before Strategy/Risk can be recomputed on restart. A nonterminal/uncertain attempt is never blindly resubmitted.
