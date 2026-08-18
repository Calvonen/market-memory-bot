# Trading system foundation

This branch introduces a broker-independent, paper-first domain layer for the AI-assisted trading system.

## Non-negotiable boundaries

1. Strategy and AI may produce structured analysis and a `TradeCandidate`.
2. Strategy and AI must never call a broker directly.
3. Every broker action must receive a `TradeProposal` containing a deterministic `RiskDecision` with status `PASS`.
4. `NO_TRADE` is a valid strategy result and can never be executed.
5. `LIVE` trading is disabled by default.
6. `PaperBroker` refuses all `LIVE` proposals.
7. Position size is determined by the Risk Engine, not AI confidence.
8. Decisions carry stable identifiers and UTC timestamps for later outcome analysis.
9. AI providers are replaceable; Strategy and Risk do not depend on any provider SDK.

## Current flow

```text
Official release
      |
      v
Release ingestion + immutable source document
      |
      v
AIEventAnalyzer
  | Groq (default cloud)
  | Ollama (local fallback)
  | OpenAI (optional opt-in fallback)
      |
      v
Fundamental 0-35 + Catalyst 0-25
      |
      + Technical 0-20
      + Market Memory 0-10
      + News/Sentiment 0-10
      |
      v
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
      PaperBroker
```

## AI provider policy

The default provider order is:

1. Groq using `openai/gpt-oss-120b`
2. local Ollama using `gpt-oss:20b`
3. OpenAI only when explicitly enabled

Groq and Ollama are called directly over HTTP and share the same strict Pydantic/JSON Schema `EventAnalysisPayload`. The normal runtime therefore has no mandatory OpenAI SDK or OpenAI API dependency.

Provider/model metadata is persisted with every event analysis so historical decisions remain auditable even when providers change.

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

## Hays FY2026 event

The first production event is `HAS.L` / Hays plc FY2026 results on 2026-08-20. The pre-event expectation is stored as immutable version 1 in the dedicated MarketAI Supabase project.

The system records every official-release check. When the FY26 release becomes available, it stores the raw official source document, fingerprints it, runs structured event analysis against the exact pre-event expectation version and only then feeds bounded evidence into Strategy Engine.

A single consensus miss or beat is never sufficient to create a trade proposal. Guidance, price reaction, technical state, market memory and the complete event analysis are required.

## Runtime AI configuration

```text
GROQ_API_KEY=...
MARKETAI_GROQ_MODEL=openai/gpt-oss-120b
MARKETAI_OLLAMA_ENABLED=true
MARKETAI_OLLAMA_URL=http://localhost:11434
MARKETAI_OLLAMA_MODEL=gpt-oss:20b

# optional only
MARKETAI_ENABLE_OPENAI_FALLBACK=false
```

## Next increments

- configure the continuous backend runtime and Groq API key
- verify the release monitor end-to-end before 20 Aug
- persist final Strategy/Risk/Trade decisions in MarketAI
- capture post-release price reaction and derive paper entry/stop/targets
- expose release and analysis state through FastAPI
- build the Expo Events/Edit and paper-trade views after backend contracts stabilize
