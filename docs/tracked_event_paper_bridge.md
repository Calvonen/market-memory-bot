# Tracked-event reaction -> PAPER bridge

The persistent tracked-event runtime and the earnings Strategy/Risk/PaperBroker path remain separate subsystems. This bridge connects them without creating a second trading path.

For a tracked earnings event, the bridge accepts only the canonical first post-event 1-minute reaction already persisted by the tracked-event worker. Before accepting that evidence it re-resolves the eToro instrument through the caller-supplied production resolver and requires the persisted instrument id, symbol, display name and market to match. The persisted broker identity must also be armed before the event.

The pre-event reference is accepted only when its capture timestamp is timezone-aware and no later than `event_at`, its kind is exactly `etoro_last_execution_pre_event_snapshot`, and its price is finite and positive. The anchored 1-minute reaction must be observed only after the candle is complete. Its return is recomputed from the persisted close/reference prices, and its direction is reconstructed using the same canonical flat threshold as `MarketReactionEngine`; stored values that disagree fail closed.

The bridge deliberately has no daily-bar fallback. If the anchored 1-minute reaction is not persisted yet, it returns `waiting_confirmation`. A valid flat reaction also remains `waiting_confirmation`. Identity, provenance, timing, price, return or direction contradictions fail closed.

The existing `run_post_release_paper()` keeps its daily event-bar behavior for existing callers, but now accepts an explicit `confirmed_reaction_pct`. Only a caller that has already validated canonical event-reaction evidence should provide that value.

This PR does not schedule events, read releases, create expectations, write paper-run rows, or alter Strategy/Risk/Broker policy. Production orchestration can wire this bridge in a follow-up once this boundary is reviewed.
