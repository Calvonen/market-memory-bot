# Trading system foundation

This branch introduces the first broker-independent domain layer for the AI-assisted trading system.

## Non-negotiable boundaries

1. Strategy and AI may produce structured analysis and a `TradeCandidate`.
2. Strategy and AI must never call a broker directly.
3. Every broker action must receive a `TradeProposal` that contains a deterministic `RiskDecision` with status `PASS`.
4. `NO_TRADE` is a valid strategy result and can never be executed.
5. `LIVE` trading is disabled by default.
6. `PaperBroker` refuses all `LIVE` proposals.
7. Position size is determined by the Risk Engine, not AI confidence.
8. Decisions carry stable identifiers and UTC timestamps so they can later be persisted and compared with realized outcomes.

## Foundation flow

```text
StrategyDecision
      |
      v
TradeCandidate
      |
      v
RiskEngine
  |       |
REJECT   PASS
          |
          v
   TradeProposal
          |
          v
      Broker
```

## Current deterministic risk checks

- kill switch
- live trading enable flag
- max open positions
- instrument exposure cap
- max daily loss
- spread cap
- volatility cap
- cooldown after a loss
- valid entry, stop and target
- stop/target direction
- minimum reward/risk
- maximum risk per trade
- maximum position size

## Hays FY2026 test event

The first event fixture is `HAS.L` / Hays plc FY2026 results on 2026-08-20. Consensus values and trading triggers are intentionally not fabricated in code; they must be filled from verified pre-event data before event analysis.

The event fixture also states explicitly that one consensus miss alone is not sufficient to create a trade proposal. Price reaction and the complete event analysis are required.

## Next increments

- connect existing `market_memory` technical and similarity modules to structured strategy inputs
- add event actuals and official-release ingestion
- persist strategy/risk/proposal records in PostgreSQL/Supabase
- add FastAPI read/write endpoints
- add paper position lifecycle and P/L accounting
- build Expo client only after the backend contracts are stable
