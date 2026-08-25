-- Once a calendar occurrence is bound to the persistent tracked-event runtime,
-- the old watchlist untrack action must not silently demote only the calendar
-- row while the runtime worker keeps monitoring in the background. Serialize
-- the guard under the same calendar-row lock used by promotion and fail closed.
begin;

create or replace function public.transition_calendar_event_status(
  input_calendar_event_id uuid,
  input_from_status text,
  input_to_status text
)
returns table (
  out_id uuid,
  out_company_name text,
  out_instrument text,
  out_market text,
  out_event_type text,
  out_occurrence_key text,
  out_scheduled_date date,
  out_source text,
  out_status text,
  out_created_at timestamptz,
  out_updated_at timestamptz,
  out_action text
)
language plpgsql as $$
declare
  existing_row public.calendar_events%rowtype;
  new_row public.calendar_events%rowtype;
begin
  select * into existing_row
  from public.calendar_events
  where id = input_calendar_event_id
  for update;

  if existing_row.id is null then
    raise exception 'calendar_event_not_found' using errcode = 'P0002';
  end if;

  -- Promotion and this transition both lock calendar_events first. Therefore
  -- a concurrent first promotion cannot race between this guard and the
  -- status update: whichever transaction obtains the calendar lock first owns
  -- the complete decision. Runtime cancellation needs its own explicit
  -- lifecycle operation; the old watchlist untrack action is not one.
  if input_to_status = 'candidate'
     and exists (
       select 1
       from public.tracked_market_events t
       where t.calendar_event_id = existing_row.id
     ) then
    raise exception 'calendar_event_runtime_bound' using errcode = 'P0001';
  end if;

  if existing_row.status = input_to_status then
    return query select
      existing_row.id, existing_row.company_name, existing_row.instrument, existing_row.market,
      existing_row.event_type, existing_row.occurrence_key, existing_row.scheduled_date,
      existing_row.source, existing_row.status,
      existing_row.created_at, existing_row.updated_at, 'noop_already'::text;
    return;
  end if;

  if existing_row.status <> input_from_status then
    raise exception 'invalid_calendar_event_transition' using errcode = 'P0001';
  end if;

  update public.calendar_events
  set status = input_to_status,
      updated_at = now()
  where id = existing_row.id
  returning * into new_row;

  return query select
    new_row.id, new_row.company_name, new_row.instrument, new_row.market,
    new_row.event_type, new_row.occurrence_key, new_row.scheduled_date,
    new_row.source, new_row.status, new_row.created_at, new_row.updated_at,
    'updated'::text;
end;
$$;

revoke all on function public.transition_calendar_event_status(uuid, text, text) from public;
grant execute on function public.transition_calendar_event_status(uuid, text, text) to service_role;

create or replace function public.calendar_runtime_untrack_guard_version()
returns integer
language sql
immutable
security invoker
as $$
  select 1;
$$;

revoke all on function public.calendar_runtime_untrack_guard_version() from public;
grant execute on function public.calendar_runtime_untrack_guard_version() to service_role;

create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 9;
$$;

revoke all on function public.tracked_event_runtime_schema_version from public;
grant execute on function public.tracked_event_runtime_schema_version to service_role;

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
  capture_tracked_market_event_pre_event_context_validated_function_exists boolean,
  validate_tracked_market_event_pre_event_context_if_current_function_exists boolean,
  fail_tracked_market_event_pre_event_deadline_if_current_function_exists boolean,
  fail_tracked_market_event_stale_context_if_current_function_exists boolean,
  promote_calendar_event_to_tracked_runtime_function_exists boolean,
  calendar_runtime_untrack_guard_version_matches boolean,
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
      'public.capture_tracked_market_event_pre_event_context_validated(uuid,jsonb,text,text,timestamptz,timestamptz)'
    ) is not null,
    to_regprocedure(
      'public.validate_tracked_market_event_pre_event_context_if_current(uuid,timestamptz)'
    ) is not null,
    to_regprocedure(
      'public.fail_tracked_market_event_pre_event_deadline_if_current(uuid,timestamptz,text,text)'
    ) is not null,
    to_regprocedure(
      'public.fail_tracked_market_event_stale_context_if_current(uuid,timestamptz,text,text)'
    ) is not null,
    to_regprocedure(
      'public.promote_calendar_event_to_tracked_runtime(uuid,text,text,text,text,date,timestamptz,text,text)'
    ) is not null,
    public.calendar_runtime_untrack_guard_version() = 1,
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;

commit;
