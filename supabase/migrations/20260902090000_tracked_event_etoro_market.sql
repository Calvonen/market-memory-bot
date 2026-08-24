-- Preserve the eToro-resolved market identity separately from the tracked
-- event's source market metadata. This nullable storage-only step is
-- deliberately backward-compatible with the currently deployed worker and
-- existing resolution/reference RPC signatures; runtime writing is added in a
-- later migration/code slice after readers are compatible.

alter table public.tracked_market_events
  add column if not exists resolved_etoro_market text null;

comment on column public.tracked_market_events.resolved_etoro_market is
  'Market/exchange label returned by the resolved eToro instrument search; nullable for legacy rows and until runtime capture is enabled.';
