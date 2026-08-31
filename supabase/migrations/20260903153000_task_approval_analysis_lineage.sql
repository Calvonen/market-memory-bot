-- Bind canonical task approval to the exact expectation lineage and close the
-- remaining claim/persistence races before Strategy/Risk/Broker execution.

alter table public.trading_tasks
  add column if not exists approved_expectation_version integer null
    check (approved_expectation_version is null or approved_expectation_version > 0);

-- Existing approved rows predate lineage binding and intentionally remain NULL.
-- They fail closed in the task-aware claim and must be cancelled/recreated.

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

  -- Serialize approval with expectation writes, analysis writes and claims for
  -- the same event so the approved lineage cannot move underneath approval.
  perform pg_advisory_xact_lock(hashtextextended(source_event, 0));

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

revoke all on function public.approve_trading_task(uuid, text)
  from public, anon, authenticated;
grant execute on function public.approve_trading_task(uuid, text)
  to service_role;

-- Every analysis mutation participates in the same per-event advisory lock used
-- by task approval and task-aware claims. This makes the claim's uniqueness
-- check stable for the duration of the claim transaction without a global table
-- lock.
create or replace function public.lock_event_ai_analysis_lineage()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  perform pg_advisory_xact_lock(hashtextextended(new.event_id, 0));
  return new;
end;
$$;

drop trigger if exists lock_event_ai_analysis_lineage
  on public.event_ai_analyses;
create trigger lock_event_ai_analysis_lineage
before insert or update on public.event_ai_analyses
for each row execute function public.lock_event_ai_analysis_lineage();

-- Replace the task-aware claim with lineage checks that are all executed while
-- holding the same event advisory lock.
create or replace function public.claim_event_paper_run_for_task(
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
  old_task_row public.trading_tasks%rowtype;
  existing_claim public.event_paper_trade_event_claims%rowtype;
  claimed public.event_paper_trade_event_claims%rowtype;
  terminal_run public.event_paper_trade_runs%rowtype;
  existing_task_run public.event_paper_trade_runs%rowtype;
  current_expectation_version integer;
  analysis_count integer;
  canonical_analysis_id uuid;
  now_value timestamptz := clock_timestamp();
begin
  if input_task_id is null then
    raise exception 'paper_run_task_missing';
  end if;
  if input_expectation_version is null or input_expectation_version < 1 then
    raise exception 'paper_run_expectation_version_invalid';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

  select version into current_expectation_version
  from public.current_event_expectations
  where event_id = input_event_id
  limit 1;
  if not found then
    raise exception 'paper_run_expectation_not_found';
  end if;
  if current_expectation_version <> input_expectation_version then
    raise exception 'paper_run_expectation_version_changed';
  end if;

  select * into task_row
  from public.trading_tasks
  where id = input_task_id
  for share;
  if not found then
    raise exception 'paper_run_task_not_found';
  end if;
  if task_row.state <> 'approved' then
    raise exception 'paper_run_task_not_approved';
  end if;
  if task_row.mode <> 'PAPER' then
    raise exception 'paper_run_task_not_paper';
  end if;
  if task_row.source_event_id <> input_event_id then
    raise exception 'paper_run_task_event_mismatch';
  end if;
  if task_row.approved_expectation_version is null
     or task_row.approved_expectation_version <> input_expectation_version then
    raise exception 'paper_run_task_expectation_lineage_changed';
  end if;

  -- The analysis write trigger above holds this same event lock, therefore no
  -- second analysis for this event/version can appear between this validation
  -- and granting the lease.
  select
    count(*)::integer,
    (array_agg(id order by created_at, id))[1]
  into analysis_count, canonical_analysis_id
  from public.event_ai_analyses
  where event_id = input_event_id
    and expectation_version = input_expectation_version;

  if analysis_count <> 1 or canonical_analysis_id <> input_analysis_id then
    raise exception 'paper_run_analysis_lineage_ambiguous';
  end if;

  select * into existing_task_run
  from public.event_paper_trade_runs
  where task_id = input_task_id
  limit 1;
  if found and existing_task_run.analysis_id <> input_analysis_id then
    raise exception 'paper_run_task_analysis_changed';
  end if;

  select * into terminal_run
  from public.event_paper_trade_runs
  where event_id = input_event_id
    and status in ('expired_no_trade', 'paper_executed')
  limit 1;
  if found then
    insert into public.event_paper_trade_event_claims(
      event_id, analysis_id, task_id, lease_expires_at, terminal_status, updated_at
    ) values (
      input_event_id,
      terminal_run.analysis_id,
      terminal_run.task_id,
      null,
      terminal_run.status,
      now_value
    )
    on conflict (event_id) do update set
      analysis_id = excluded.analysis_id,
      task_id = excluded.task_id,
      lease_expires_at = null,
      terminal_status = excluded.terminal_status,
      updated_at = excluded.updated_at
    returning * into claimed;
    return next claimed;
    return;
  end if;

  select * into existing_claim
  from public.event_paper_trade_event_claims
  where event_id = input_event_id
  for update;

  if not found then
    insert into public.event_paper_trade_event_claims(
      event_id, analysis_id, task_id, claim_token, claimed_at,
      lease_expires_at, terminal_status, updated_at
    ) values (
      input_event_id,
      input_analysis_id,
      input_task_id,
      input_claim_token,
      now_value,
      now_value + make_interval(secs => greatest(input_lease_seconds, 1)),
      null,
      now_value
    )
    returning * into claimed;
    return next claimed;
    return;
  end if;

  if existing_claim.terminal_status is not null then
    return next existing_claim;
    return;
  end if;

  if existing_claim.task_id = input_task_id
     and existing_claim.analysis_id <> input_analysis_id then
    raise exception 'paper_run_task_analysis_changed';
  end if;

  if existing_claim.task_id = input_task_id
     and existing_claim.analysis_id = input_analysis_id
     and existing_claim.claim_token = input_claim_token then
    update public.event_paper_trade_event_claims
    set
      lease_expires_at = now_value + make_interval(secs => greatest(input_lease_seconds, 1)),
      updated_at = now_value
    where event_id = input_event_id
    returning * into claimed;
    return next claimed;
    return;
  end if;

  if existing_claim.lease_expires_at is not null
     and existing_claim.lease_expires_at > now_value then
    return next existing_claim;
    return;
  end if;

  if existing_claim.task_id is not null
     and existing_claim.task_id <> input_task_id then
    select * into old_task_row
    from public.trading_tasks
    where id = existing_claim.task_id
    for share;
    if not found or old_task_row.state <> 'cancelled' then
      raise exception 'paper_run_task_claim_conflict';
    end if;
  end if;

  update public.event_paper_trade_event_claims
  set
    analysis_id = input_analysis_id,
    task_id = input_task_id,
    claim_token = input_claim_token,
    claimed_at = now_value,
    lease_expires_at = now_value + make_interval(secs => greatest(input_lease_seconds, 1)),
    terminal_status = null,
    updated_at = now_value
  where event_id = input_event_id
  returning * into claimed;

  return next claimed;
end;
$$;

revoke all on function public.claim_event_paper_run_for_task(text, uuid, uuid, integer, uuid, integer)
  from public, anon, authenticated;
grant execute on function public.claim_event_paper_run_for_task(text, uuid, uuid, integer, uuid, integer)
  to service_role;

-- Task-aware persistence is a separate RPC so legacy paper callers keep their
-- historical contract. The exact task + analysis + token lease is revalidated
-- under the event lock immediately before delegating to the existing atomic save.
create or replace function public.save_event_paper_trade_result_for_task(input_payload jsonb)
returns setof public.event_paper_trade_runs
language plpgsql
security invoker
set search_path = public
as $$
declare
  effective_event_id text := input_payload->>'event_id';
  effective_analysis_id uuid := nullif(input_payload->>'analysis_id', '')::uuid;
  effective_claim_token uuid := nullif(input_payload->>'claim_token', '')::uuid;
  effective_task_id uuid := nullif(input_payload->>'task_id', '')::uuid;
  claim_row public.event_paper_trade_event_claims%rowtype;
  task_row public.trading_tasks%rowtype;
  terminal_owner public.event_paper_trade_runs%rowtype;
  now_value timestamptz := clock_timestamp();
begin
  if effective_event_id is null or btrim(effective_event_id) = ''
     or effective_analysis_id is null
     or effective_claim_token is null
     or effective_task_id is null then
    raise exception 'paper_run_task_save_identity_missing';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(effective_event_id, 0));

  select * into terminal_owner
  from public.event_paper_trade_runs
  where event_id = effective_event_id
    and status in ('expired_no_trade', 'paper_executed')
  limit 1;
  if found then
    return next terminal_owner;
    return;
  end if;

  select * into claim_row
  from public.event_paper_trade_event_claims
  where event_id = effective_event_id
  for update;

  if not found
     or claim_row.terminal_status is not null
     or claim_row.task_id <> effective_task_id
     or claim_row.analysis_id <> effective_analysis_id
     or claim_row.claim_token <> effective_claim_token
     or claim_row.lease_expires_at is null
     or claim_row.lease_expires_at < now_value then
    return query
    select * from public.event_paper_trade_runs
    where analysis_id = effective_analysis_id
    limit 1;
    return;
  end if;

  select * into task_row
  from public.trading_tasks
  where id = effective_task_id
  for share;
  if not found
     or task_row.state <> 'approved'
     or task_row.mode <> 'PAPER'
     or task_row.source_event_id <> effective_event_id then
    return query
    select * from public.event_paper_trade_runs
    where analysis_id = effective_analysis_id
    limit 1;
    return;
  end if;

  return query
  select * from public.save_event_paper_trade_result(input_payload);
end;
$$;

revoke all on function public.save_event_paper_trade_result_for_task(jsonb)
  from public, anon, authenticated;
grant execute on function public.save_event_paper_trade_result_for_task(jsonb)
  to service_role;
