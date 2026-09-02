-- Bind an explicit per-event position-value ceiling to the execution task and
-- add an approval RPC that atomically verifies the exact expectation version
-- the user confirmed in the UI.

alter table public.trading_tasks
  add column if not exists max_position_value_usd numeric null;

alter table public.trading_tasks
  drop constraint if exists trading_tasks_max_position_value_usd_check;
alter table public.trading_tasks
  add constraint trading_tasks_max_position_value_usd_check
  check (
    max_position_value_usd is null
    or (
      max_position_value_usd <> 'NaN'::numeric
      and max_position_value_usd > 0
    )
  );

-- Keep the existing five-argument create_trading_task() intact for existing
-- control paths. This overload is used by the mobile PAPER-permission flow and
-- makes the requested cap immutable once the active task is created.
create or replace function public.create_trading_task(
  input_tracked_event_id uuid,
  input_source_event_id text,
  input_instrument text,
  input_mode text,
  input_actor text,
  input_max_position_value_usd numeric
)
returns public.trading_tasks
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  event_row public.tracked_market_events%rowtype;
  canonical_source_event_id text;
  actor text := btrim(input_actor);
  instrument_value text := upper(btrim(input_instrument));
  mode_value text := upper(btrim(input_mode));
  cap_value numeric := input_max_position_value_usd;
  created public.trading_tasks%rowtype;
begin
  if input_tracked_event_id is null then
    raise exception 'trading_task_invalid_tracked_event_id';
  end if;
  if input_source_event_id is null or btrim(input_source_event_id) = '' then
    raise exception 'trading_task_invalid_source_event_id';
  end if;
  if instrument_value = '' then
    raise exception 'trading_task_invalid_instrument';
  end if;
  if mode_value not in ('PAPER', 'LIVE') then
    raise exception 'trading_task_invalid_mode';
  end if;
  if actor is null or length(actor) not between 1 and 200 then
    raise exception 'trading_task_invalid_actor';
  end if;
  if cap_value is null or cap_value = 'NaN'::numeric or cap_value <= 0 then
    raise exception 'trading_task_invalid_position_cap';
  end if;

  select * into event_row
  from public.tracked_market_events
  where id = input_tracked_event_id;
  if not found then
    raise exception 'trading_task_tracked_event_not_found';
  end if;

  canonical_source_event_id := case
    when event_row.calendar_event_id is not null then 'calendar:' || event_row.calendar_event_id::text
    else 'tracked:' || event_row.id::text
  end;

  if input_source_event_id <> canonical_source_event_id then
    raise exception 'trading_task_event_identity_mismatch';
  end if;
  if upper(btrim(event_row.instrument)) <> instrument_value then
    raise exception 'trading_task_instrument_mismatch';
  end if;

  begin
    insert into public.trading_tasks (
      tracked_event_id,
      source_event_id,
      instrument,
      mode,
      state,
      created_by,
      max_position_value_usd
    ) values (
      input_tracked_event_id,
      canonical_source_event_id,
      instrument_value,
      mode_value,
      'pending',
      actor,
      cap_value
    )
    returning * into created;
    return created;
  exception
    when unique_violation then
      select * into created
      from public.trading_tasks
      where tracked_event_id = input_tracked_event_id
        and mode = mode_value
        and state in ('pending', 'approved')
      limit 2;

      if not found then
        raise exception 'trading_task_creation_conflict';
      end if;
      if created.source_event_id <> canonical_source_event_id
         or upper(btrim(created.instrument)) <> instrument_value
         or created.created_by <> actor
         or created.max_position_value_usd is distinct from cap_value then
        raise exception 'trading_task_creation_conflict';
      end if;
      return created;
  end;
end;
$$;

-- Existing two-argument approval remains available to existing operator paths.
-- The mobile control path uses this overload so the approval and lineage check
-- happen under the same per-event advisory lock.
create or replace function public.approve_trading_task(
  input_task_id uuid,
  input_actor text,
  input_expected_expectation_version integer
)
returns public.trading_tasks
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  actor text := btrim(input_actor);
  source_event text;
  current_version integer;
  approved public.trading_tasks%rowtype;
begin
  if input_task_id is null then
    raise exception 'trading_task_invalid_id';
  end if;
  if actor is null or length(actor) not between 1 and 200 then
    raise exception 'trading_task_invalid_actor';
  end if;
  if input_expected_expectation_version is null or input_expected_expectation_version < 1 then
    raise exception 'trading_task_invalid_expected_expectation_version';
  end if;

  select source_event_id into source_event
  from public.trading_tasks
  where id = input_task_id;
  if not found then
    raise exception 'trading_task_not_found';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(source_event, 0));

  select version into current_version
  from public.current_event_expectations
  where event_id = source_event
  limit 1;
  if not found then
    raise exception 'trading_task_expectation_not_found';
  end if;
  if current_version <> input_expected_expectation_version then
    raise exception 'trading_task_expectation_version_changed';
  end if;

  update public.trading_tasks
  set
    state = 'approved',
    approved_by = actor,
    approved_at = now(),
    approved_expectation_version = current_version
  where id = input_task_id and state = 'pending'
  returning * into approved;

  if found then
    return approved;
  end if;
  raise exception 'trading_task_not_pending';
end;
$$;

revoke all on function public.create_trading_task(uuid, text, text, text, text, numeric)
  from public, anon, authenticated;
grant execute on function public.create_trading_task(uuid, text, text, text, text, numeric)
  to service_role;

revoke all on function public.approve_trading_task(uuid, text, integer)
  from public, anon, authenticated;
grant execute on function public.approve_trading_task(uuid, text, integer)
  to service_role;
