-- Proposal-only storage for assistant-prepared MarketAI event plans.
--
-- This migration deliberately does not materialize anything into canonical
-- tracked events, expectations, official release sources, trading tasks, risk,
-- or broker state. The assistant can stage a proposal; later backend code owns
-- validation/materialization and the user separately owns execution approval.

create table if not exists public.assistant_event_proposals (
  id uuid primary key default gen_random_uuid(),
  proposal_key text not null unique,
  payload jsonb not null,
  requested_execution_mode text not null default 'demo'
    check (requested_execution_mode in ('demo', 'live')),
  status text not null default 'ready_for_review'
    check (status in (
      'draft',
      'ready_for_review',
      'rejected',
      'approved_for_materialization',
      'materialized'
    )),
  created_by text not null default 'assistant',
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (btrim(proposal_key) <> ''),
  check (jsonb_typeof(payload) = 'object')
);

alter table public.assistant_event_proposals enable row level security;

revoke all on table public.assistant_event_proposals from anon, authenticated;
grant select, insert, update on table public.assistant_event_proposals to service_role;

create index if not exists assistant_event_proposals_status_created_idx
  on public.assistant_event_proposals(status, created_at);

comment on table public.assistant_event_proposals is
  'Non-execution staging for assistant-prepared plans. Canonical materialization and execution approval happen elsewhere.';
