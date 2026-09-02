-- Bind an explicit per-event position-value ceiling to execution authority and
-- make the mobile PAPER permission a single atomic operation: verify the exact
-- expectation version the user confirmed, replace any stale/different active
-- task, and persist the approved task under the same per-event advisory lock.

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

create or replace function public.approve_paper_trading_task_for_event(
  input_tracked_event_id uuid,
  input_source_event_id text,
  input_instrument text,
  input_actor text,
  input_expected_expectation_version integer,
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
  cap_value numeric := input_max_position_value_usd;
  current_version integer;
  active_task public.trading_tasks%rowtype;
  approved public.trading_tasks%rowtype;
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
  if actor is null or length(actor) not between 1 and 200 then
    raise exception 'trading_task_invalid_actor';
  end if;
  if input_expected_expectation_version is null or input_expected_expectation_version < 1 then
    raise exception 'trading_task_invalid_expected_expectation_version';
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

  -- Serialize this complete permission decision with expectation writes,
  -- analysis lineage writes and task-aware execution claims for the same event.
  perform pg_advisory_xact_lock(hashtextextended(canonical_source_event_id, 0));

  select version into current_version
  from public.current_event_expectations
  where event_id = canonical_source_event_id
  limit 1;
  if not found then
    raise exception 'trading_task_expectation_not_found';
  end if;
  if current_version <> input_expected_expectation_version then
    raise exception 'trading_task_expectation_version_changed';
  end if;

  select * into active_task
  from public.trading_tasks
  where tracked_event_id = input_tracked_event_id
    and mode = 'PAPER'
    and state in ('pending', 'approved')
  limit 1
  for update;

  -- Exact retry of an already committed permission is idempotent.
  if found
     and active_task.state = 'approved'
     and active_task.source_event_id = canonical_source_event_id
     and upper(btrim(active_task.instrument)) = instrument_value
     and active_task.approved_expectation_version = input_expected_expectation_version
     and active_task.max_position_value_usd is not distinct from cap_value then
    return active_task;
  end if;

  -- A changed cap, stale lineage, or pending predecessor is historical intent,
  -- not authority for this newly confirmed permission. Replace it atomically.
  if found then
    update public.trading_tasks
    set
      state = 'cancelled',
      cancelled_by = actor,
      cancelled_at = now()
    where id = active_task.id;
  end if;

  insert into public.trading_tasks (
    tracked_event_id,
    source_event_id,
    instrument,
    mode,
    state,
    created_by,
    created_at,
    approved_by,
    approved_at,
    approved_expectation_version,
    max_position_value_usd
  ) values (
    input_tracked_event_id,
    canonical_source_event_id,
    instrument_value,
    'PAPER',
    'approved',
    actor,
    now(),
    actor,
    now(),
    input_expected_expectation_version,
    cap_value
  )
  returning * into approved;

  return approved;
end;
$$;

revoke all on function public.approve_paper_trading_task_for_event(uuid, text, text, text, integer, numeric)
  from public, anon, authenticated;
grant execute on function public.approve_paper_trading_task_for_event(uuid, text, text, text, integer, numeric)
  to service_role;
