-- Close the remaining task-bound claim races before Strategy/Risk/Broker can run.
-- The tracked-event orchestrator must use the six-argument task-aware claim below.

revoke execute on function public.claim_event_paper_run_for_task(text, uuid, uuid, uuid, integer)
  from service_role;
revoke all on function public.claim_event_paper_run_for_task(text, uuid, uuid, uuid, integer)
  from public, anon, authenticated;

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
  now_value timestamptz := clock_timestamp();
begin
  if input_task_id is null then
    raise exception 'paper_run_task_missing';
  end if;
  if input_expectation_version is null or input_expectation_version < 1 then
    raise exception 'paper_run_expectation_version_invalid';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

  -- Re-read the canonical expectation while holding the same event lock used by
  -- the claim. A stale Python read must never authorize Strategy/Risk/Broker.
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

  -- A task is immutable execution authority for one analysis lineage. If the
  -- canonical expectation produced a new analysis, require cancel + new task
  -- rather than allowing the old approval to re-enter the broker and then hit
  -- the unique task audit at persistence time.
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

  -- Same exact authority/analysis/token is a normal retry and renews the lease.
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

  -- An active lease cannot be displaced, regardless of task identity or token.
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

  -- Expired legacy claim or expired claim from a cancelled task may transfer to
  -- the freshly approved replacement. The caller must still prove ownership by
  -- matching task_id + analysis_id + claim_token after this RPC returns.
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
