-- Terminal "the event's deadline passed before its pre-event context was
-- ready" failures must be decided against the current row, not against a
-- tracked-event object the worker read earlier from list_runnable().
--
-- The worker's deadline decision previously ran as read-then-write: read the
-- row, compare now() to the event_at it carried, then mark_failed(). Between
-- that read and the write, upsert_tracked_market_event can reschedule the
-- event into the future - and the write would still terminal-fail an event
-- whose deadline has not actually passed, which is unrecoverable because
-- 'failed' is terminal.
--
-- This RPC makes the whole decision atomic: it locks the row, requires the
-- caller's expected version, confirms the event is still awaiting a pre-event
-- baseline at all, re-checks the deadline against the row's own current
-- event_at, and only then writes 'failed'. A rescheduled or otherwise changed
-- row raises a version conflict (retryable - the next poll re-evaluates
-- whatever the event now is) instead of being terminal-failed.
--
-- The invariant the guard encodes: an event may only be deadline-failed while
-- pre_event_market_context IS NULL. A committed capture is proof the
-- preparation this failure is about succeeded, whatever the caller observed.
--
-- Keep the pre-deploy schema gate (verify_tracked_event_runtime_schema(),
-- scripts/verify_supabase_schema.py) in lockstep with this migration, same as
-- every earlier tracked-event runtime migration: bump the version marker and
-- extend the verifier so a deploy against a database missing this RPC fails
-- closed instead of starting a worker whose deadline failures would silently
-- fall back to a racy path that no longer exists in code.
begin;

create or replace function public.fail_tracked_market_event_pre_event_deadline_if_current(
  input_event_id uuid,
  input_expected_updated_at timestamptz,
  input_actor text,
  input_error text
)
returns public.tracked_market_events
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
begin
  if input_expected_updated_at is null then
    raise exception 'input_expected_updated_at is required';
  end if;
  if nullif(btrim(input_actor), '') is null then
    raise exception 'input_actor is required';
  end if;
  if nullif(btrim(input_error), '') is null then
    raise exception 'input_error is required';
  end if;

  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;

  -- Any concurrent write - a reschedule above all - invalidates the deadline
  -- decision the caller made from its own read.
  if existing_row.updated_at is distinct from input_expected_updated_at then
    raise exception 'tracked_market_event_version_conflict';
  end if;

  -- Only an event still awaiting its pre-event baseline can be failed this
  -- way. A row that has since started monitoring or captured a reference is
  -- past this decision entirely.
  --
  -- pre_event_market_context is part of that test, not an afterthought: the
  -- capture RPC can commit and still surface an exception to the worker (a
  -- lost response, or the acquisition thread raising while re-reading after a
  -- successful capture). If that lands at or after event_at, the worker asks
  -- to terminal-fail an event whose preparation actually succeeded. Requiring
  -- the context to still be null makes that impossible to write: the RPC
  -- refuses, the failure is retryable, and the next poll continues down the
  -- persisted-context restart/revalidation path instead.
  if existing_row.status <> 'tracked'
     or existing_row.reference_price is not null
     or existing_row.pre_event_market_context is not null then
    raise exception 'tracked_market_event_not_pre_event_failable';
  end if;

  -- The authority on whether the deadline passed is the locked row's own
  -- event_at, never the caller's copy of it.
  if pg_catalog.clock_timestamp() < existing_row.event_at then
    raise exception 'tracked_market_event_pre_event_deadline_not_reached';
  end if;

  update public.tracked_market_events
  set status = 'failed',
      last_error = left(input_error, 1000),
      updated_by = input_actor,
      updated_at = now()
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.fail_tracked_market_event_pre_event_deadline_if_current(
  uuid, timestamptz, text, text
) from public;
grant execute on function public.fail_tracked_market_event_pre_event_deadline_if_current(
  uuid, timestamptz, text, text
) to service_role;

create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 5;
$$;

revoke all on function public.tracked_event_runtime_schema_version from public;
grant execute on function public.tracked_event_runtime_schema_version to service_role;

-- The OUT signature changed again in this migration, so the old verifier must
-- be dropped before recreating it with the extra column.
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
