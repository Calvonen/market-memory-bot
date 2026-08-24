# Pre-event market context

This is an observation-only data contract for the last complete trading session before a tracked event.

The first slice intentionally defines only the versioned Python model. It does not change the tracked-event worker, Supabase schema, reaction calculations, StrategyEngine, RiskEngine or any broker path.

Version 1 stores the previous complete session's date, OHLC values, full-session return and an optional late-session return. The session close is exposed as the canonical closed-market fallback reference price for a later runtime slice.

The intended follow-up sequence is deliberately small:

1. persist this snapshot on `tracked_market_events` with capture-once semantics;
2. add one read-only historical-session acquisition method to the eToro market-data provider;
3. capture the previous complete session ahead of `event_at` and use its persisted close only when the live pre-event `lastExecution` is unavailable/non-positive;
4. expose the context through the read API/mobile card only after the runtime data is proven live.

A later acquisition slice may populate `late_session_return_pct` from intraday candles. Keeping it optional avoids coupling the first persistence/runtime change to an intraday historical-data implementation.
