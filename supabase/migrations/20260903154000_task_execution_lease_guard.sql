-- Align task approval/claim lineage locking with the canonical expectation writer
-- and add a last-moment execution-lease guard for broker entry.

create or replace function public.approve_trading_task(
  input_task_id uuid,
  input_actor text
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

  select source_event_id into source_event
  from public.trading_tasks
  where id = input_task_id;
  if not found then
    raise exception 'trading_task_not_found';
  end if;

  -- Canonical expectation writers use salt 1 for this event lineage lock.
  perform pg_advisory_xact_lock(hashtextextended(source_event, 1));

  select version into current_version
  from public.current_event_expectations
  where event_id = source_event
  limit 1;
  if not found then
    raise exception 'trading_task_expectation_not_found';
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

create or replace function public.lock_event_ai_analysis_lineage()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  -- Use the same salt-1 lineage lock as expectation writes/approval/claims.
  perform pg_advisory_xact_lock(hashtextextended(new.event_id, 1));
  return new;
end;
$$;

-- Keep the previous six-argument claim implementation as the salt-0 paper-run
-- primitive, but force tracked execution through this wrapper. The wrapper first
-- acquires the expectation writer's salt-1 lineage lock; the delegated function
-- then acquires salt 0. That fixed lock order is also used by the broker guard.
create or replace function public.claim_event_paper_run_for_task_v2(
  input_event_id text,
  input_analysis_id uuid,
  input_task_id uuid,
  input_expectation_version integer,
  input_claim_token uuid,
  input_lease_seconds integer
)
returns setof public.event_paper_trade_event_claims
language plpgsql
security invoker
set search_path = public
as $$
begin
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));
  return query
  select * from public.claim_event_paper_run_for_task(
    input_event_id,
    input_analysis_id,
    input_task_id,
    input_expectation_version,
    input_claim_token,
    input_lease_seconds
  );
end;
$$;

revoke execute on function public.claim_event_paper_run_for_task(text, uuid, uuid, integer, uuid, integer)
  from service_role;
revoke all on function public.claim_event_paper_run_for_task_v2(text, uuid, uuid, integer, uuid, integer)
  from public, anon, authenticated;
grant execute on function public.claim_event_paper_run_for_task_v2(text, uuid, uuid, integer, uuid, integer)
  to service_role;

create or replace function public.revalidate_event_paper_run_task_lease(
  input_event_id text,
  input_analysis_id uuid,
  input_task_id uuid,
  input_expectation_version integer,
  input_claim_token uuid,
  input_lease_seconds integer
)
returns setof public.event_paper_trade_event_claims
language plpgsql
security invoker
set search_path = public
as $$
declare
  task_row public.trading_tasks%rowtype;
  claim_row public.event_paper_trade_event_claims%rowtype;
  current_version integer;
  analysis_count integer;
  canonical_analysis_id uuid;
  now_value timestamptz := clock_timestamp();
begin
  if input_task_id is null
     or input_analysis_id is null
     or input_claim_token is null
     or input_expectation_version is null
     or input_expectation_version < 1 then
    raise exception 'paper_run_execution_lease_identity_invalid';
  end if;

  -- Match the fixed lock order used by the task-aware claim wrapper.
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

  select version into current_version
  from public.current_event_expectations
  where event_id = input_event_id
  limit 1;
  if not found or current_version <> input_expectation_version then
    return;
  end if;

  select * into task_row
  from public.trading_tasks
  where id = input_task_id
  for share;
  if not found
     or task_row.state <> 'approved'
     or task_row.mode <> 'PAPER'
     or task_row.source_event_id <> input_event_id
     or task_row.approved_expectation_version is null
     or task_row.approved_expectation_version <> input_expectation_version then
    return;
  end if;

  -- Analysis writers hold the same salt-1 lock, so this uniqueness check cannot
  -- become stale before this transaction renews the execution lease.
  select
    count(*)::integer,
    (array_agg(id order by created_at, id))[1]
  into analysis_count, canonical_analysis_id
  from public.event_ai_analyses
  where event_id = input_event_id
    and expectation_version = input_expectation_version;
  if analysis_count <> 1 or canonical_analysis_id <> input_analysis_id then
    return;
  end if;

  select * into claim_row
  from public.event_paper_trade_event_claims
  where event_id = input_event_id
  for update;
  if not found
     or claim_row.terminal_status is not null
     or claim_row.task_id <> input_task_id
     or claim_row.analysis_id <> input_analysis_id
     or claim_row.claim_token <> input_claim_token
     or claim_row.lease_expires_at is null
     or claim_row.lease_expires_at <= now_value then
    return;
  end if;

  update public.event_paper_trade_event_claims
  set
    lease_expires_at = now_value + make_interval(secs => greatest(input_lease_seconds, 1)),
    updated_at = now_value
  where event_id = input_event_id
    and task_id = input_task_id
    and analysis_id = input_analysis_id
    and claim_token = input_claim_token
  returning * into claim_row;

  return next claim_row;
end;
$$;

revoke all on function public.revalidate_event_paper_run_task_lease(text, uuid, uuid, integer, uuid, integer)
  from public, anon, authenticated;
grant execute on function public.revalidate_event_paper_run_task_lease(text, uuid, uuid, integer, uuid, integer)
  to service_role;

-- An approved task that currently owns a live execution lease cannot be
-- cancelled underneath the broker boundary. Cancellation becomes available as
-- soon as the lease expires or the run becomes terminal.
create or replace function public.cancel_trading_task(
  input_task_id uuid,
  input_actor text
)
returns public.trading_tasks
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  actor text := btrim(input_actor);
  task_row public.trading_tasks%rowtype;
  active_claim boolean;
  cancelled public.trading_tasks%rowtype;
begin
  if input_task_id is null then
    raise exception 'trading_task_invalid_id';
  end if;
  if actor is null or length(actor) not between 1 and 200 then
    raise exception 'trading_task_invalid_actor';
  end if;

  select * into task_row
  from public.trading_tasks
  where id = input_task_id
  for update;
  if not found then
    raise exception 'trading_task_not_found';
  end if;

  if task_row.state = 'approved' then
    perform pg_advisory_xact_lock(hashtextextended(task_row.source_event_id, 0));
    select exists (
      select 1
      from public.event_paper_trade_event_claims
      where task_id = input_task_id
        and terminal_status is null
        and lease_expires_at > clock_timestamp()
    ) into active_claim;
    if active_claim then
      raise exception 'trading_task_execution_lease_active';
    end if;
  end if;

  update public.trading_tasks
  set state = 'cancelled', cancelled_by = actor, cancelled_at = now()
  where id = input_task_id and state in ('pending', 'approved')
  returning * into cancelled;

  if found then
    return cancelled;
  end if;
  raise exception 'trading_task_already_cancelled';
end;
$$;

revoke all on function public.approve_trading_task(uuid, text) from public, anon, authenticated;
revoke all on function public.cancel_trading_task(uuid, text) from public, anon, authenticated;
grant execute on function public.approve_trading_task(uuid, text) to service_role;
grant execute on function public.cancel_trading_task(uuid, text) to service_role;
