-- Resolve broker identity before an event enters its timing-critical monitoring path.
-- The long-running worker may perform discovery well ahead of event_at, then stores
-- one exact eToro identity here. Reference capture and live monitoring reuse that
-- identity and never run a full catalog search at the event boundary.

alter table public.tracked_market_events
  add column if not exists resolution_armed_at timestamptz null;

alter table public.tracked_market_events
  add column if not exists resolution_armed_by text null;

alter table public.tracked_market_events
  add constraint tracked_market_events_resolution_is_complete
  check (
    (resolved_etoro_instrument_id is null
      and resolved_etoro_symbol is null
      and resolved_etoro_display_name is null
      and resolution_armed_at is null
      and resolution_armed_by is null)
    or
    (resolved_etoro_instrument_id is not null
      and resolved_etoro_symbol is not null
      and resolved_etoro_display_name is not null
      and resolution_armed_at is not null
      and resolution_armed_by is not null)
  );

create or replace function public.arm_tracked_market_event_resolution(
  input_event_id uuid,
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
  if input_etoro_instrument_id is null or input_etoro_instrument_id <= 0
     or btrim(coalesce(input_etoro_symbol, '')) = ''
     or btrim(coalesce(input_etoro_display_name, '')) = ''
     or btrim(coalesce(input_actor, '')) = '' then
    raise exception 'invalid_tracked_market_event_resolution';
  end if;

  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;
  if existing_row.status <> 'tracked' or existing_row.reference_price is not null then
    raise exception 'tracked_market_event_resolution_locked';
  end if;

  if existing_row.resolved_etoro_instrument_id is not null then
    if existing_row.resolved_etoro_instrument_id = input_etoro_instrument_id
       and upper(existing_row.resolved_etoro_symbol) = upper(input_etoro_symbol)
       and upper(existing_row.resolved_etoro_display_name) = upper(input_etoro_display_name) then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_resolution_conflict';
  end if;

  update public.tracked_market_events
  set resolved_etoro_instrument_id = input_etoro_instrument_id,
      resolved_etoro_symbol = input_etoro_symbol,
      resolved_etoro_display_name = input_etoro_display_name,
      resolution_armed_at = now(),
      resolution_armed_by = input_actor,
      updated_by = input_actor,
      updated_at = now(),
      last_error = null
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.arm_tracked_market_event_resolution from public;
grant execute on function public.arm_tracked_market_event_resolution to service_role;

-- Reference capture now requires the already-armed broker identity and refuses
-- to mutate it. This prevents a late reference fetch from silently retargeting
-- the event to a different eToro instrument.
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
  if existing_row.resolved_etoro_instrument_id is null
     or existing_row.resolution_armed_at is null then
    raise exception 'tracked_market_event_resolution_missing';
  end if;
  if existing_row.resolved_etoro_instrument_id <> input_etoro_instrument_id
     or upper(existing_row.resolved_etoro_symbol) <> upper(input_etoro_symbol)
     or upper(existing_row.resolved_etoro_display_name) <> upper(input_etoro_display_name) then
    raise exception 'tracked_market_event_resolution_conflict';
  end if;
  if input_reference_price is null or input_reference_price <= 0 then
    raise exception 'invalid_reference_price';
  end if;
  if input_reference_captured_at > existing_row.event_at then
    raise exception 'reference_captured_after_event';
  end if;
  if existing_row.reference_price is not null then
    if existing_row.reference_price = input_reference_price
       and existing_row.reference_captured_at = input_reference_captured_at then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_reference_locked';
  end if;

  update public.tracked_market_events
  set reference_price = input_reference_price,
      reference_captured_at = input_reference_captured_at,
      reference_kind = input_reference_kind,
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

create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 2;
$$;

revoke all on function public.tracked_event_runtime_schema_version from public;
grant execute on function public.tracked_event_runtime_schema_version to service_role;

-- The OUT signature changed in this migration, so PostgreSQL requires the old
-- verifier to be dropped before recreating it with the extra columns.
drop function if exists public.verify_tracked_event_runtime_schema();

create function public.verify_tracked_event_runtime_schema()
returns table (
  tracked_market_events_table_exists boolean,
  tracked_market_event_reactions_table_exists boolean,
  upsert_tracked_market_event_function_exists boolean,
  arm_tracked_market_event_resolution_function_exists boolean,
  capture_tracked_market_event_reference_function_exists boolean,
  capture_tracked_market_event_reaction_anchor_function_exists boolean,
  runtime_schema_version integer
)
language sql
stable
security invoker
as $$
  select
    to_regclass('public.tracked_market_events') is not null,
    to_regclass('public.tracked_market_event_reactions') is not null,
    to_regprocedure(
      'public.upsert_tracked_market_event(text,text,text,text,text,text,text,timestamptz,text,text,uuid)'
    ) is not null,
    to_regprocedure(
      'public.arm_tracked_market_event_resolution(uuid,bigint,text,text,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_reference(uuid,numeric,timestamptz,text,bigint,text,text,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_reaction_anchor(uuid,timestamptz,text)'
    ) is not null,
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;