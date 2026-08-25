-- Atomically promote one calendar/watchlist occurrence into the persistent
-- tracked-event runtime. Timing is resolved by trusted backend code before
-- this RPC is called; this transaction then locks the calendar row, rechecks
-- the exact identity/date used for that timing decision, creates/reuses the
-- persistent runtime event through the canonical upsert function, and only
-- then marks the calendar row tracked. Any failure rolls back both writes.
begin;

create or replace function public.promote_calendar_event_to_tracked_runtime(
  input_calendar_event_id uuid,
  input_expected_instrument text,
  input_expected_event_type text,
  input_expected_source text,
  input_expected_occurrence_key text,
  input_expected_scheduled_date date,
  input_event_at timestamptz,
  input_event_time_status text,
  input_actor text
)
returns table (
  out_event_id uuid,
  out_action text,
  out_calendar_status text
)
language plpgsql
security invoker
as $$
declare
  calendar_row public.calendar_events%rowtype;
  existing_runtime public.tracked_market_events%rowtype;
  promoted_event_id uuid;
  promoted_action text;
begin
  if input_event_at is null then
    raise exception 'calendar_runtime_event_at_required';
  end if;
  if input_event_time_status not in ('confirmed', 'estimated', 'unknown') then
    raise exception 'invalid_event_time_status';
  end if;
  if nullif(btrim(input_actor), '') is null then
    raise exception 'calendar_runtime_actor_required';
  end if;

  select * into calendar_row
  from public.calendar_events
  where id = input_calendar_event_id
  for update;

  if calendar_row.id is null then
    raise exception 'calendar_event_not_found' using errcode = 'P0002';
  end if;

  if calendar_row.status not in ('candidate', 'tracked') then
    raise exception 'calendar_event_not_promotable';
  end if;

  -- The backend resolved timing from exactly these fields before entering the
  -- transaction. If provider sync changed the occurrence meanwhile, refuse
  -- to promote stale timing and let the caller resolve again from the new row.
  if calendar_row.instrument is distinct from input_expected_instrument
     or calendar_row.event_type is distinct from input_expected_event_type
     or calendar_row.source is distinct from input_expected_source
     or calendar_row.occurrence_key is distinct from input_expected_occurrence_key
     or calendar_row.scheduled_date is distinct from input_expected_scheduled_date then
    raise exception 'calendar_event_changed_before_promotion';
  end if;

  -- A retry after a successful promotion must be a true no-op. In particular,
  -- a later Finnhub hour change must not silently move the already-created
  -- runtime event through upsert_tracked_market_event while it is still in
  -- TRACKED/reference-null state. Explicit rescheduling belongs in a separate
  -- lifecycle operation, not in an idempotent watchlist button retry.
  select * into existing_runtime
  from public.tracked_market_events
  where calendar_event_id = calendar_row.id
  for update;

  if existing_runtime.id is not null then
    if existing_runtime.instrument is distinct from upper(replace(calendar_row.instrument, ' ', ''))
       or existing_runtime.kind is distinct from calendar_row.event_type
       or existing_runtime.source is distinct from calendar_row.source
       or existing_runtime.external_key is distinct from ('calendar:' || calendar_row.id::text) then
      raise exception 'calendar_runtime_binding_identity_conflict';
    end if;

    if calendar_row.status = 'candidate' then
      update public.calendar_events
      set status = 'tracked',
          updated_at = now()
      where id = calendar_row.id;
      calendar_row.status := 'tracked';
    end if;

    return query select existing_runtime.id, 'noop_existing'::text, calendar_row.status;
    return;
  end if;

  select u.out_id, u.out_action
    into promoted_event_id, promoted_action
  from public.upsert_tracked_market_event(
    calendar_row.company_name,
    calendar_row.instrument,
    calendar_row.market,
    calendar_row.source,
    'calendar:' || calendar_row.id::text,
    calendar_row.event_type,
    calendar_row.company_name || ' ' || calendar_row.event_type,
    input_event_at,
    input_event_time_status,
    input_actor,
    calendar_row.id
  ) as u;

  if promoted_event_id is null then
    raise exception 'calendar_runtime_promotion_upsert_returned_no_event';
  end if;

  if calendar_row.status = 'candidate' then
    update public.calendar_events
    set status = 'tracked',
        updated_at = now()
    where id = calendar_row.id;
    calendar_row.status := 'tracked';
  end if;

  return query select promoted_event_id, promoted_action, calendar_row.status;
end;
$$;

revoke all on function public.promote_calendar_event_to_tracked_runtime(
  uuid, text, text, text, text, date, timestamptz, text, text
) from public;
grant execute on function public.promote_calendar_event_to_tracked_runtime(
  uuid, text, text, text, text, date, timestamptz, text, text
) to service_role;

create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 8;
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
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;

commit;
