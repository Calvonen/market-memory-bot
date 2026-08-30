# Tracked-event reaction -> PAPER bridge

The persistent tracked-event runtime and the earnings Strategy/Risk/PaperBroker path remain separate subsystems. This bridge connects them without creating a second trading path.

For a tracked earnings event, the bridge accepts only the canonical first post-event 1-minute reaction already persisted by the tracked-event worker. It validates release identity, instrument identity, reaction anchor, reference price, timestamps, direction and return before delegating to the existing `run_post_release_paper()` pipeline.

The bridge deliberately has no daily-bar fallback. If the anchored 1-minute reaction is not persisted yet, it returns `waiting_confirmation`. A flat reaction also remains `waiting_confirmation`. Identity/reference contradictions fail closed.

The existing `run_post_release_paper()` keeps its daily event-bar behavior for existing callers, but now accepts an explicit `confirmed_reaction_pct`. Only a caller that has already validated canonical event-reaction evidence should provide that value.

This PR does not schedule events, read releases, create expectations, write paper-run rows, or alter Strategy/Risk/Broker policy. Production orchestration can wire this bridge in a follow-up once this boundary is reviewed.
