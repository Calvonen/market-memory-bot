# Editable event configuration

Pre-event expectations are configuration/data, not trading-engine code.

## Target storage

A dedicated market-system Supabase project should become the source of truth for editable event setup. Do not use the existing EnergiaZen project.

Recommended split:

- `events`: event identity, instrument, date, status and provenance
- `event_expectations`: versioned consensus values and ranges
- `event_scenarios`: bull/base/bear scenario text
- `event_triggers`: editable strategy thresholds such as FY27 operating-profit and price-reaction levels
- `event_kpis`: important KPI definitions and display order
- `event_expectation_versions`: immutable snapshots used by historical strategy decisions

Strategy decisions should reference the exact expectation version used at decision time. Editing a current event creates a new version instead of rewriting historical decision context.

## Editing flow

The Expo app can later expose an Events/Edit screen:

1. open an upcoming event
2. edit consensus, ranges, KPI importance, scenarios or triggers
3. save through the FastAPI backend
4. backend validates values and writes a new Supabase expectation version
5. workers read the active version through `EventExpectationRepository`

The mobile client should not write directly to trading tables with a privileged database key. FastAPI owns validation and writes; the Expo client uses authenticated API calls and only public/publishable Supabase credentials where Realtime/Auth are needed.

## Safety boundary

Editable event values can influence Strategy Engine evidence but cannot:

- change Risk Engine hard limits
- enable live trading
- bypass kill switch
- call a broker directly
- rewrite historical strategy/risk decisions

Risk defaults and broker safety remain code/configuration controlled separately from event research data.

## Current Hays seed

Until the dedicated Supabase project exists, `tests/fixtures/hays_fy2026.py` contains the verified FY26/FY27 company-compiled consensus and clearly separated strategy trigger thresholds. It is temporary seed/test data, not the intended production source of truth.
