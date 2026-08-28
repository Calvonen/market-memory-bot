-- Create the release-pipeline shell from canonical tracked-event identity instead
-- of requiring calendar ownership. Calendar-bound events retain their existing
-- calendar:<uuid> release identity for compatibility; calendar-less producers use
-- tracked:<tracked_event_id>. No consensus/KPI expectations are inferred.
begin;

create or replace function public.ensure_tracked_event_release_shell(
  input_tracked_event_id uuid
)
returns table (
  out_release_event_id text,
  out_action text
)
language plpgsql
security invoker
as $$
declare
  tracked_row public.tracked_market_events%rowtype;
  calendar_row public.calendar_events%rowtype;
  initial_calendar_event_id uuid;
  existing_market_event public.market_events%rowtype;
  release_event_id text;
  release_event_name text;
  expectation_exists boolean;
  shell_action text := 'noop_existing';
begin
  -- Probe the tracked row without taking a row lock so calendar-bound paths can
  -- acquire locks in the same order as calendar promotion: calendar first,
  -- tracked event second. This avoids a calendar<->tracked deadlock while the
  -- second locked read below protects against a binding change between reads.
  select calendar_event_id into initial_calendar_event_id
  from public.tracked_market_events
  where id = input_tracked_event_id;

  if not found then
    raise exception 'tracked_event_not_found' using errcode = 'P0002';
  end if;

  if initial_calendar_event_id is not null then
    select * into calendar_row
    from public.calendar_events
    where id = initial_calendar_event_id
    for update;

    if calendar_row.id is null then
      raise exception 'tracked_release_calendar_event_not_found' using errcode = 'P0002';
    end if;
  end if;

  select * into tracked_row
  from public.tracked_market_events
  where id = input_tracked_event_id
  for update;

  if tracked_row.id is null then
    raise exception 'tracked_event_not_found' using errcode = 'P0002';
  end if;
  if tracked_row.calendar_event_id is distinct from initial_calendar_event_id then
    raise exception 'tracked_release_calendar_binding_changed_during_lock';
  end if;
  if tracked_row.kind <> 'earnings' then
    raise exception 'tracked_event_not_release_shell_eligible';
  end if;
  if tracked_row.event_date is null then
    raise exception 'tracked_event_release_date_required';
  end if;

  if tracked_row.calendar_event_id is not null then
    -- Calendar is only a producer. Before preserving its legacy release identity,
    -- prove that the calendar row describes this exact canonical tracked event.
    if upper(replace(calendar_row.instrument, ' ', '')) is distinct from tracked_row.instrument
       or calendar_row.event_type is distinct from tracked_row.kind
       or calendar_row.scheduled_date is distinct from tracked_row.event_date
       or calendar_row.source is distinct from tracked_row.source
       or tracked_row.external_key is distinct from ('calendar:' || calendar_row.id::text) then
      raise exception 'tracked_release_calendar_binding_identity_conflict';
    end if;

    release_event_id := 'calendar:' || calendar_row.id::text;
    release_event_name := calendar_row.company_name || ' ' || calendar_row.event_type;
  else
    release_event_id := 'tracked:' || tracked_row.id::text;
    release_event_name := tracked_row.instrument || ' ' || tracked_row.kind;
  end if;

  select * into existing_market_event
  from public.market_events
  where event_id = release_event_id
  for update;

  if existing_market_event.event_id is null then
    insert into public.market_events (
      event_id,
      instrument,
      event_name,
      scheduled_date,
      status
    ) values (
      release_event_id,
      tracked_row.instrument,
      release_event_name,
      tracked_row.event_date,
      'scheduled'
    );
    shell_action := 'inserted_market_event';
  else
    if existing_market_event.instrument is distinct from tracked_row.instrument
       or existing_market_event.event_name is distinct from release_event_name
       or existing_market_event.scheduled_date is distinct from tracked_row.event_date then
      raise exception 'tracked_release_shell_identity_conflict';
    end if;
  end if;

  select exists (
    select 1
    from public.event_expectation_versions e
    where e.event_id = release_event_id
  ) into expectation_exists;

  if not expectation_exists then
    insert into public.event_expectation_versions (
      event_id,
      version,
      source_name,
      source_url,
      source_as_of,
      consensus,
      important_kpis,
      bull_case,
      base_case,
      bear_case,
      triggers,
      invalidation_conditions,
      change_note
    ) values (
      release_event_id,
      1,
      case
        when tracked_row.calendar_event_id is not null
          then 'calendar:' || tracked_row.source || ':automatic-release-shell'
        else 'tracked:' || tracked_row.source || ':automatic-release-shell'
      end,
      null,
      null,
      '{}'::jsonb,
      '[]'::jsonb,
      '[]'::jsonb,
      '[]'::jsonb,
      '[]'::jsonb,
      '{}'::jsonb,
      '[]'::jsonb,
      'automatic canonical tracked-event release shell; no consensus or KPI expectations inferred'
    );
    if shell_action = 'noop_existing' then
      shell_action := 'inserted_expectation';
    else
      shell_action := 'inserted';
    end if;
  end if;

  return query select release_event_id, shell_action;
end;
$$;

revoke all on function public.ensure_tracked_event_release_shell(uuid) from public;
grant execute on function public.ensure_tracked_event_release_shell(uuid) to service_role;

create or replace function public.ensure_tracked_event_release_shell_after_date_write()
returns trigger
language plpgsql
security invoker
as $$
begin
  perform * from public.ensure_tracked_event_release_shell(new.id);
  return new;
end;
$$;

revoke all on function public.ensure_tracked_event_release_shell_after_date_write() from public;

drop trigger if exists tracked_market_events_release_shell_after_date_write
  on public.tracked_market_events;
create trigger tracked_market_events_release_shell_after_date_write
  after insert or update of event_date on public.tracked_market_events
  for each row
  when (new.kind = 'earnings' and new.event_date is not null)
  execute function public.ensure_tracked_event_release_shell_after_date_write();

-- Backfill shells only for canonical tracked earnings that already have an
-- explicit local event_date. The canonical function validates calendar binding
-- directly and therefore remains safe for calendar rows that have advanced past
-- the old candidate/tracked watchlist statuses.
do $$
declare
  target record;
begin
  for target in
    select id
    from public.tracked_market_events
    where kind = 'earnings'
      and event_date is not null
  loop
    perform * from public.ensure_tracked_event_release_shell(target.id);
  end loop;
end;
$$;

create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 12;
$$;

revoke all on function public.tracked_event_runtime_schema_version from public;
grant execute on function public.tracked_event_runtime_schema_version to service_role;

drop function if exists public.verify_tracked_event_runtime_schema();

create function public.verify_tracked_event_runtime_schema()
returns table (
  tracked_market_events_table_exists boolean,
  tracked_market_event_reactions_table_exists boolean,
  tracked_market_event_event_date_column_exists boolean,
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
  ensure_calendar_release_shell_function_exists boolean,
  calendar_release_shell_version_matches boolean,
  ensure_tracked_event_release_shell_function_exists boolean,
  tracked_event_release_shell_trigger_exists boolean,
  runtime_schema_version integer
)
language sql
stable
security invoker
as $$
  select
    to_regclass('public.tracked_market_events') is not null,
    to_regclass('public.tracked_market_event_reactions') is not null,
    exists (
      select 1
      from information_schema.columns
      where table_schema = 'public'
        and table_name = 'tracked_market_events'
        and column_name = 'event_date'
        and data_type = 'date'
    ),
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
    to_regprocedure('public.ensure_calendar_release_shell(uuid)') is not null,
    public.calendar_release_shell_version() = 1,
    to_regprocedure('public.ensure_tracked_event_release_shell(uuid)') is not null,
    exists (
      select 1
      from pg_trigger t
      join pg_class c on c.oid = t.tgrelid
      join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public'
        and c.relname = 'tracked_market_events'
        and t.tgname = 'tracked_market_events_release_shell_after_date_write'
        and not t.tgisinternal
    ),
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;

commit;
