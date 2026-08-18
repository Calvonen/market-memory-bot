create table if not exists public.event_paper_trade_runs (
  id uuid primary key default gen_random_uuid(),
  event_id text not null references public.market_events(event_id) on delete cascade,
  expectation_version integer not null,
  source_document_id uuid null references public.event_source_documents(id) on delete set null,
  analysis_id uuid not null references public.event_ai_analyses(id) on delete cascade,
  status text not null check (status in ('waiting_confirmation','paper_executed')),
  message text not null,
  strategy jsonb null,
  risk jsonb null,
  paper_order jsonb null,
  first_seen_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (analysis_id)
);

alter table public.event_paper_trade_runs enable row level security;
revoke all on table public.event_paper_trade_runs from anon, authenticated;
grant select, insert, update on table public.event_paper_trade_runs to service_role;

create index if not exists event_paper_trade_runs_event_id_idx
  on public.event_paper_trade_runs(event_id, updated_at desc);
