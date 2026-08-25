-- Allow a pre-event context whose latest session is the event's own local
-- market date, but only through a canonical path that proves the session had
-- actually closed strictly before the event.
--
-- The base RPC keeps the strict date rule. Direct/legacy callers without an
-- exchange-calendar close proof may only persist session_date < event local
-- date. The validated RPC below is the sole same-day path.
begin;

create or replace function public.capture_tracked_market_event_pre_event_context(
  input_event_id uuid,
  input_pre_event_market_context jsonb,
  input_market_timezone text,
  input_actor text
)
returns public.tracked_market_events
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
  event_local_date date;
  snapshot_session_date date;
  snapshot_previous_session_date date;
begin
  if not public.is_valid_pre_event_market_context_v1(input_pre_event_market_context) then
    raise exception 'invalid_pre_event_market_context';
  end if;
  if nullif(btrim(input_market_timezone), '') is null then
    raise exception 'input_market_timezone is required';
  end if;
  if nullif(btrim(input_actor), '') is null then
    raise exception 'input_actor is required';
  end if;

  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;

  begin
    event_local_date := (existing_row.event_at at time zone input_market_timezone)::date;
    snapshot_session_date := (input_pre_event_market_context ->> 'session_date')::date;
    snapshot_previous_session_date :=
      (input_pre_event_market_context ->> 'previous_session_date')::date;
  exception when others then
    raise exception 'invalid_market_timezone_or_session_date';
  end;

  if snapshot_session_date >= event_local_date then
    raise exception 'pre_event_market_context_not_before_event';
  end if;

  if snapshot_previous_session_date >= snapshot_session_date then
    raise exception 'pre_event_market_context_sessions_out_of_order';
  end if;

  if existing_row.pre_event_market_context is not null then
    if existing_row.pre_event_market_context = input_pre_event_market_context then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_pre_event_context_locked';
  end if;

  if existing_row.status not in ('tracked', 'monitoring') then
    raise exception 'tracked_market_event_not_context_captureable';
  end if;

  update public.tracked_market_events
  set pre_event_market_context = input_pre_event_market_context,
      updated_by = input_actor,
      updated_at = now()
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.capture_tracked_market_event_pre_event_context from public;
grant execute on function public.capture_tracked_market_event_pre_event_context to service_role;

create or replace function public.capture_tracked_market_event_pre_event_context_validated(
  input_event_id uuid,
  input_pre_event_market_context jsonb,
  input_market_timezone text,
  input_actor text,
  input_expected_updated_at timestamptz,
  input_session_close timestamptz
)
returns public.tracked_market_events
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
  event_local_date date;
  snapshot_session_date date;
  snapshot_previous_session_date date;
  session_close_local_date date;
begin
  if not public.is_valid_pre_event_market_context_v1(input_pre_event_market_context) then
    raise exception 'invalid_pre_event_market_context';
  end if;
  if nullif(btrim(input_market_timezone), '') is null then
    raise exception 'input_market_timezone is required';
  end if;
  if nullif(btrim(input_actor), '') is null then
    raise exception 'input_actor is required';
  end if;
  if input_expected_updated_at is null then
    raise exception 'input_expected_updated_at is required';
  end if;
  if input_session_close is null then
    raise exception 'input_session_close is required';
  end if;

  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;

  -- Exact retries are idempotent even after the deadline because they create
  -- no new snapshot and merely confirm the immutable value already accepted.
  if existing_row.pre_event_market_context = input_pre_event_market_context then
    return existing_row;
  end if;

  if pg_catalog.clock_timestamp() >= existing_row.event_at then
    raise exception 'tracked_market_event_pre_event_context_deadline_passed';
  end if;

  if existing_row.updated_at is distinct from input_expected_updated_at then
    raise exception 'tracked_market_event_version_conflict';
  end if;

  begin
    event_local_date := (existing_row.event_at at time zone input_market_timezone)::date;
    snapshot_session_date := (input_pre_event_market_context ->> 'session_date')::date;
    snapshot_previous_session_date :=
      (input_pre_event_market_context ->> 'previous_session_date')::date;
    session_close_local_date := (input_session_close at time zone input_market_timezone)::date;
  exception when others then
    raise exception 'invalid_market_timezone_or_session_date';
  end;

  if snapshot_session_date > event_local_date then
    raise exception 'pre_event_market_context_not_before_event';
  end if;

  if snapshot_previous_session_date >= snapshot_session_date then
    raise exception 'pre_event_market_context_sessions_out_of_order';
  end if;
  if snapshot_previous_session_date >= event_local_date then
    raise exception 'pre_event_market_context_not_before_event';
  end if;

  if session_close_local_date <> snapshot_session_date then
    raise exception 'pre_event_market_context_session_close_mismatch';
  end if;

  -- Strict ordering is required. A session whose official close timestamp is
  -- exactly event_at is not proven to have completed before the event.
  if input_session_close >= existing_row.event_at then
    raise exception 'pre_event_market_context_session_not_closed_before_event';
  end if;
  if input_session_close > pg_catalog.clock_timestamp() then
    raise exception 'pre_event_market_context_session_not_closed_yet';
  end if;

  if existing_row.pre_event_market_context is not null then
    raise exception 'tracked_market_event_pre_event_context_locked';
  end if;

  if existing_row.status not in ('tracked', 'monitoring') then
    raise exception 'tracked_market_event_not_context_captureable';
  end if;

  update public.tracked_market_events
  set pre_event_market_context = input_pre_event_market_context,
      updated_by = input_actor,
      updated_at = now()
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.capture_tracked_market_event_pre_event_context_validated(
  uuid, jsonb, text, text, timestamptz, timestamptz
) from public;
grant execute on function public.capture_tracked_market_event_pre_event_context_validated(
  uuid, jsonb, text, text, timestamptz, timestamptz
) to service_role;

create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 6;
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
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;

commit;
