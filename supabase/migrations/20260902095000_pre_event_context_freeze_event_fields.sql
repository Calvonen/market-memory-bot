-- Freeze the tracked-event fields a persisted pre_event_market_context is
-- grounded on (event_at, market, event_time_status, title), the same way
-- reference capture already freezes them once reference_price is set.
--
-- Without this, upsert_tracked_market_event could still change event_at after
-- a valid pre_event_market_context had been captured and revalidated against
-- the row it was captured for - reopening exactly the race the worker-side
-- revalidation (persisted_pre_event_market_context_is_current +
-- validate_tracked_market_event_pre_event_context_if_current) was added to
-- close. Enforcing the freeze here, at the one write path that can change
-- these fields, means no further Python-side "check again" step is needed:
-- once pre_event_market_context is non-null, upsert_tracked_market_event
-- itself refuses to change the fields it depends on, for both the
-- newly-acquired-context path and any later restart/revalidation path.
--
-- Keep the pre-deploy schema gate (verify_tracked_event_runtime_schema(),
-- scripts/verify_supabase_schema.py) in lockstep with this migration, same
-- as every earlier tracked-event runtime migration: bump the version marker
-- and extend the verifier so a deploy against a database missing this
-- invariant (or the pre-event-context RPCs it protects) fails closed instead
-- of starting a worker that can silently monitor a stale snapshot.
begin;

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

  -- A persisted pre_event_market_context is grounded on the exact event_at
  -- (via its session_date/previous_session_date) it was captured and
  -- revalidated for, plus market (session-profile lookup) and event_time_status
  -- and title, which are surfaced alongside it. Once it is non-null, freeze
  -- these the same way reference_price already does, so nothing can ever make
  -- the persisted snapshot stale after the fact.
  if existing_row.status <> 'tracked'
     or existing_row.reference_price is not null
     or existing_row.pre_event_market_context is not null then
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

create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 4;
$$;

revoke all on function public.tracked_event_runtime_schema_version from public;
grant execute on function public.tracked_event_runtime_schema_version to service_role;

-- The OUT signature changed again in this migration, so the old verifier must
-- be dropped before recreating it with the extra pre-event-context columns.
drop function if exists public.verify_tracked_event_runtime_schema();

create function public.verify_tracked_event_runtime_schema()
returns table (
  tracked_market_events_table_exists boolean,
  tracked_market_event_reactions_table_exists boolean,
  upsert_tracked_market_event_function_exists boolean,
  arm_tracked_market_event_resolution_function_exists boolean,
  capture_tracked_market_event_reference_function_exists boolean,
  capture_tracked_market_event_reaction_anchor_function_exists boolean,
  capture_tracked_market_event_config_snapshot_function_exists boolean,
  capture_tracked_market_event_pre_event_context_function_exists boolean,
  capture_tracked_market_event_pre_event_context_if_current_function_exists boolean,
  validate_tracked_market_event_pre_event_context_if_current_function_exists boolean,
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
    to_regprocedure(
      'public.capture_tracked_market_event_config_snapshot(uuid,jsonb,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_pre_event_context(uuid,jsonb,text,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_pre_event_context_if_current(uuid,jsonb,text,text,timestamptz)'
    ) is not null,
    to_regprocedure(
      'public.validate_tracked_market_event_pre_event_context_if_current(uuid,timestamptz)'
    ) is not null,
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;

commit;
