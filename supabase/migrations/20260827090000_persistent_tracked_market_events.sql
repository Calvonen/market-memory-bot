-- Persistent runtime events for generic market-reaction monitoring.
--
-- This is deliberately separate from calendar_events (candidate/watchlist state)
-- and from market_events/event_expectations (the older expectation/trading path).
-- A row here means "MarketAI should monitor this concrete scheduled event"; it
-- does not imply that a trade exists or may be executed.

create table if not exists public.tracked_market_events (
  id uuid primary key default gen_random_uuid(),
  tracked_instrument_id text not null default replace(gen_random_uuid()::text, '-', ''),
  calendar_event_id uuid null references public.calendar_events(id) on delete set null,
  company_name text not null default '',
  instrument text not null,
  market text not null,
  source text not null,
  external_key text not null,
  kind text not null,
  title text not null default '',
  event_at timestamptz not null,
  event_time_status text not null default 'unknown' check (
    event_time_status in ('confirmed', 'estimated', 'unknown')
  ),
  status text not null default 'tracked' check (
    status in ('tracked', 'monitoring', 'completed', 'cancelled', 'failed')
  ),
  resolved_etoro_instrument_id bigint null,
  resolved_etoro_symbol text null,
  resolved_etoro_display_name text null,
  reference_price numeric null check (reference_price is null or reference_price > 0),
  reference_captured_at timestamptz null,
  reference_kind text null,
  started_at timestamptz null,
  completed_at timestamptz null,
  last_error text null,
  created_by text not null,
  updated_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source, external_key),
  unique (calendar_event_id),
  check ((reference_price is null) = (reference_captured_at is null)),
  check (reference_captured_at is null or reference_captured_at <= event_at)
);

create index if not exists tracked_market_events_status_event_at_idx
  on public.tracked_market_events(status, event_at);

create table if not exists public.tracked_market_event_reactions (
  id uuid primary key default gen_random_uuid(),
  tracked_market_event_id uuid not null references public.tracked_market_events(id) on delete cascade,
  interval_minutes integer not null check (interval_minutes in (1, 5, 15)),
  candle_start timestamptz not null,
  reference_price numeric not null check (reference_price > 0),
  close_price numeric not null check (close_price > 0),
  return_pct numeric not null,
  direction text not null,
  evolution text not null,
  observed_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (tracked_market_event_id, interval_minutes, candle_start)
);

create index if not exists tracked_market_event_reactions_event_idx
  on public.tracked_market_event_reactions(tracked_market_event_id, candle_start);

alter table public.tracked_market_events enable row level security;
alter table public.tracked_market_event_reactions enable row level security;
revoke all on table public.tracked_market_events from anon, authenticated;
revoke all on table public.tracked_market_event_reactions from anon, authenticated;
grant select, insert, update on table public.tracked_market_events to service_role;
grant select, insert, update on table public.tracked_market_event_reactions to service_role;

-- Canonical idempotent writer used by trusted agents/tooling. The unique
-- (source, external_key) identity prevents retries or multiple agents from
-- creating duplicate runtime events. Once a reference has been captured or
-- monitoring has begun, material event identity/timing is locked fail-closed.
create or replace function public.upsert_tracked_market_event(
  input_company_name text,
  input_instrument text,
  input_market text,
  input_source text,
  input_external_key text,
  input_kind text,
  input_title text,
  input_event_at timestamptz,
  input_event_time_status text,
  input_actor text,
  input_calendar_event_id uuid default null
)
returns table (
  out_id uuid,
  out_tracked_instrument_id text,
  out_calendar_event_id uuid,
  out_company_name text,
  out_instrument text,
  out_market text,
  out_source text,
  out_external_key text,
  out_kind text,
  out_title text,
  out_event_at timestamptz,
  out_event_time_status text,
  out_status text,
  out_reference_price numeric,
  out_reference_captured_at timestamptz,
  out_created_by text,
  out_updated_by text,
  out_created_at timestamptz,
  out_updated_at timestamptz,
  out_action text
)
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
begin
  if btrim(coalesce(input_instrument, '')) = ''
     or btrim(coalesce(input_market, '')) = ''
     or btrim(coalesce(input_source, '')) = ''
     or btrim(coalesce(input_external_key, '')) = ''
     or btrim(coalesce(input_kind, '')) = ''
     or btrim(coalesce(input_actor, '')) = '' then
    raise exception 'invalid_tracked_market_event_identity';
  end if;
  if input_event_time_status not in ('confirmed', 'estimated', 'unknown') then
    raise exception 'invalid_event_time_status';
  end if;

  insert into public.tracked_market_events (
    calendar_event_id, company_name, instrument, market, source, external_key,
    kind, title, event_at, event_time_status, created_by, updated_by
  ) values (
    input_calendar_event_id, coalesce(input_company_name, ''), upper(replace(input_instrument, ' ', '')),
    input_market, input_source, input_external_key, input_kind, coalesce(input_title, ''),
    input_event_at, input_event_time_status, input_actor, input_actor
  )
  on conflict (source, external_key) do nothing
  returning * into saved_row;

  if saved_row.id is not null then
    return query select
      saved_row.id, saved_row.tracked_instrument_id, saved_row.calendar_event_id,
      saved_row.company_name, saved_row.instrument, saved_row.market, saved_row.source,
      saved_row.external_key, saved_row.kind, saved_row.title, saved_row.event_at,
      saved_row.event_time_status, saved_row.status, saved_row.reference_price,
      saved_row.reference_captured_at, saved_row.created_by, saved_row.updated_by,
      saved_row.created_at, saved_row.updated_at, 'inserted'::text;
    return;
  end if;

  select * into existing_row
  from public.tracked_market_events
  where source = input_source and external_key = input_external_key
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_upsert_race_unresolved';
  end if;

  if existing_row.instrument <> upper(replace(input_instrument, ' ', ''))
     or existing_row.kind <> input_kind
     or (existing_row.calendar_event_id is distinct from input_calendar_event_id) then
    raise exception 'tracked_market_event_identity_conflict';
  end if;

  if existing_row.status <> 'tracked' or existing_row.reference_price is not null then
    if existing_row.event_at is distinct from input_event_at
       or existing_row.market is distinct from input_market
       or existing_row.event_time_status is distinct from input_event_time_status
       or existing_row.title is distinct from coalesce(input_title, '') then
      raise exception 'tracked_market_event_locked';
    end if;
    return query select
      existing_row.id, existing_row.tracked_instrument_id, existing_row.calendar_event_id,
      existing_row.company_name, existing_row.instrument, existing_row.market, existing_row.source,
      existing_row.external_key, existing_row.kind, existing_row.title, existing_row.event_at,
      existing_row.event_time_status, existing_row.status, existing_row.reference_price,
      existing_row.reference_captured_at, existing_row.created_by, existing_row.updated_by,
      existing_row.created_at, existing_row.updated_at, 'noop_locked'::text;
    return;
  end if;

  update public.tracked_market_events
  set company_name = coalesce(input_company_name, ''),
      market = input_market,
      title = coalesce(input_title, ''),
      event_at = input_event_at,
      event_time_status = input_event_time_status,
      updated_by = input_actor,
      updated_at = now(),
      last_error = null
  where id = existing_row.id
  returning * into saved_row;

  return query select
    saved_row.id, saved_row.tracked_instrument_id, saved_row.calendar_event_id,
    saved_row.company_name, saved_row.instrument, saved_row.market, saved_row.source,
    saved_row.external_key, saved_row.kind, saved_row.title, saved_row.event_at,
    saved_row.event_time_status, saved_row.status, saved_row.reference_price,
    saved_row.reference_captured_at, saved_row.created_by, saved_row.updated_by,
    saved_row.created_at, saved_row.updated_at, 'updated'::text;
end;
$$;

revoke all on function public.upsert_tracked_market_event from public;
grant execute on function public.upsert_tracked_market_event to service_role;

-- Capture the pre-event baseline exactly once. A reference captured after the
-- event would contaminate reaction measurement, so this RPC rejects it even
-- if a buggy worker/agent attempts the write.
create or replace function public.capture_tracked_market_event_reference(
  input_event_id uuid,
  input_reference_price numeric,
  input_reference_captured_at timestamptz,
  input_reference_kind text,
  input_etoro_instrument_id bigint,
  input_etoro_symbol text,
  input_etoro_display_name text,
  input_actor text
)
returns public.tracked_market_events
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
begin
  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;
  if existing_row.status not in ('tracked', 'monitoring') then
    raise exception 'tracked_market_event_not_referenceable';
  end if;
  if input_reference_price is null or input_reference_price <= 0 then
    raise exception 'invalid_reference_price';
  end if;
  if input_reference_captured_at > existing_row.event_at then
    raise exception 'reference_captured_after_event';
  end if;
  if existing_row.reference_price is not null then
    if existing_row.reference_price = input_reference_price
       and existing_row.reference_captured_at = input_reference_captured_at
       and existing_row.resolved_etoro_instrument_id = input_etoro_instrument_id then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_reference_locked';
  end if;

  update public.tracked_market_events
  set reference_price = input_reference_price,
      reference_captured_at = input_reference_captured_at,
      reference_kind = input_reference_kind,
      resolved_etoro_instrument_id = input_etoro_instrument_id,
      resolved_etoro_symbol = input_etoro_symbol,
      resolved_etoro_display_name = input_etoro_display_name,
      updated_by = input_actor,
      updated_at = now(),
      last_error = null
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.capture_tracked_market_event_reference from public;
grant execute on function public.capture_tracked_market_event_reference to service_role;
