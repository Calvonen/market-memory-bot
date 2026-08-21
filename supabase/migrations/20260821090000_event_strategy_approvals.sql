-- Audit trail for the strategy-draft preview -> approve -> persist flow.
-- Insert-only: records the approval act itself (who/what, when, which draft
-- fingerprint, which base expectation version it was checked against), kept
-- separate from event_expectation_versions which only records the resulting
-- snapshot, not the approval that produced it.
create table if not exists public.event_strategy_approvals (
  id uuid primary key default gen_random_uuid(),
  event_id text not null references public.market_events(event_id) on delete cascade,
  expectation_version integer not null,
  base_expectation_version integer not null,
  draft_fingerprint text not null,
  approved_by text not null,
  approved_via text not null,
  change_note text not null,
  created_at timestamptz not null default now()
);

alter table public.event_strategy_approvals enable row level security;
revoke all on table public.event_strategy_approvals from anon, authenticated;
grant select, insert on table public.event_strategy_approvals to service_role;

create index if not exists event_strategy_approvals_event_id_idx
  on public.event_strategy_approvals(event_id, created_at desc);
