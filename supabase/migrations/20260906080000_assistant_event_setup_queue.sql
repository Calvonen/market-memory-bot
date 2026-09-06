-- Safe ingress queue for assistant-prepared event setups.
--
-- The assistant may enqueue a reviewed setup package through the backend-only
-- Supabase connection, but this table has no direct Strategy/Risk/Broker
-- authority. A seesam-hub worker must claim the row and materialize it through
-- the existing canonical tracked-event, release-source and expectation stores.
-- PAPER execution remains separately gated by the existing trading-task
-- approval flow.

create table if not exists public.assistant_event_setup_requests (
  id uuid primary key default gen_random_uuid(),
  request_key text not null unique,
  company_name text not null,
  instrument text not null,
  market text not null,
  kind text not null default 'earnings',
  title text not null,
  scheduled_date date not null,
  event_at timestamptz not null,
  event_time_status text not null check (event_time_status in ('confirmed', 'estimated')),
  source_kind text not null check (source_kind in ('direct_url', 'results_page')),
  source_url text not null,
  source_title text,
  strategy_payload jsonb not null default '{}'::jsonb,
  change_note text not null,
  status text not null default 'pending' check (status in ('pending', 'processing', 'completed', 'failed')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  claimed_at timestamptz,
  completed_at timestamptz,
  tracked_event_id uuid references public.tracked_market_events(id) on delete set null,
  release_event_id text,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.assistant_event_setup_requests enable row level security;
revoke all on table public.assistant_event_setup_requests from anon, authenticated;
grant select, insert, update on table public.assistant_event_setup_requests to service_role;

create index if not exists assistant_event_setup_requests_status_idx
  on public.assistant_event_setup_requests(status, created_at);

create or replace function public.claim_assistant_event_setup_request()
returns setof public.assistant_event_setup_requests
language plpgsql
security invoker
set search_path = public
as $$
declare
  claimed_id uuid;
begin
  select q.id
    into claimed_id
  from public.assistant_event_setup_requests q
  where q.status = 'pending'
     or (q.status = 'processing' and q.claimed_at < now() - interval '10 minutes')
  order by q.created_at, q.id
  for update skip locked
  limit 1;

  if claimed_id is null then
    return;
  end if;

  return query
  update public.assistant_event_setup_requests q
     set status = 'processing',
         attempt_count = q.attempt_count + 1,
         claimed_at = now(),
         last_error = null,
         updated_at = now()
   where q.id = claimed_id
  returning q.*;
end;
$$;

revoke all on function public.claim_assistant_event_setup_request() from public;
grant execute on function public.claim_assistant_event_setup_request() to service_role;

comment on table public.assistant_event_setup_requests is
  'Non-execution staging queue for assistant-prepared event setups; PAPER authority is never stored here.';