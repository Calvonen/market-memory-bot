-- Recover a broker order that was durably completed but whose terminal paper-run
-- persistence was interrupted. A restarted worker must never recompute Strategy/Risk
-- and potentially hide or mismatch an already-created order.

create or replace function public.begin_event_paper_broker_attempt(
  input_event_id text,
  input_analysis_id uuid,
  input_task_id uuid,
  input_expectation_version integer,
  input_claim_token uuid,
  input_execution_token uuid,
  input_lease_seconds integer
)
returns table (
  can_execute boolean,
  attempt_status text,
  order_payload jsonb
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  task_row public.trading_tasks%rowtype;
  claim_row public.event_paper_trade_event_claims%rowtype;
  attempt_row public.event_paper_broker_attempts%rowtype;
  current_version integer;
  analysis_count integer;
  canonical_analysis_id uuid;
  now_value timestamptz := clock_timestamp();
begin
  if input_event_id is null or btrim(input_event_id) = ''
     or input_analysis_id is null
     or input_task_id is null
     or input_expectation_version is null
     or input_expectation_version < 1
     or input_claim_token is null
     or input_execution_token is null
     or input_lease_seconds is null
     or input_lease_seconds < 1 then
    raise exception 'paper_broker_attempt_identity_invalid';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

  select version into current_version
  from public.current_event_expectations
  where event_id = input_event_id
  limit 1;
  if not found or current_version <> input_expectation_version then
    raise exception 'paper_broker_attempt_expectation_changed';
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
    raise exception 'paper_broker_attempt_task_not_authorized';
  end if;

  select count(*)::integer, (array_agg(id order by created_at, id))[1]
  into analysis_count, canonical_analysis_id
  from public.event_ai_analyses
  where event_id = input_event_id
    and expectation_version = input_expectation_version;
  if analysis_count <> 1 or canonical_analysis_id <> input_analysis_id then
    raise exception 'paper_broker_attempt_analysis_ambiguous';
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
    raise exception 'paper_broker_attempt_lease_not_owned';
  end if;

  insert into public.event_paper_broker_attempts (
    task_id, event_id, analysis_id, expectation_version, claim_token,
    execution_token, status, started_at
  ) values (
    input_task_id, input_event_id, input_analysis_id, input_expectation_version,
    input_claim_token, input_execution_token, 'started', now_value
  )
  on conflict do nothing;

  select * into attempt_row
  from public.event_paper_broker_attempts
  where event_id = input_event_id
  for update;
  if not found then
    raise exception 'paper_broker_attempt_reservation_missing';
  end if;

  -- Canonical identity is stable across worker restarts. A completed attempt may
  -- legitimately carry the prior process claim token, so return its durable order
  -- after the current lease above has been revalidated.
  if attempt_row.task_id <> input_task_id
     or attempt_row.analysis_id <> input_analysis_id
     or attempt_row.expectation_version <> input_expectation_version then
    raise exception 'paper_broker_attempt_identity_conflict';
  end if;
  if attempt_row.status = 'completed' then
    return query select false, attempt_row.status, attempt_row.order_payload;
    return;
  end if;

  -- A nonterminal attempt still belongs to the exact process that reserved it.
  if attempt_row.claim_token <> input_claim_token then
    return query select false, attempt_row.status, null::jsonb;
    return;
  end if;
  if attempt_row.execution_token <> input_execution_token then
    return query select false, attempt_row.status, null::jsonb;
    return;
  end if;

  update public.event_paper_trade_event_claims
  set lease_expires_at = greatest(
        lease_expires_at,
        now_value + make_interval(secs => greatest(input_lease_seconds, 1))
      ),
      updated_at = now_value
  where event_id = input_event_id
    and task_id = input_task_id
    and analysis_id = input_analysis_id
    and claim_token = input_claim_token;

  return query select true, attempt_row.status, null::jsonb;
end;
$$;

-- Fold a completed broker attempt into the canonical terminal paper run while
-- the current task lease is owned. This happens at claim time, before Python can
-- recompute Strategy/Risk with changed market or portfolio inputs.
create or replace function public.recover_completed_event_paper_broker_attempt(
  input_event_id text,
  input_analysis_id uuid,
  input_task_id uuid,
  input_expectation_version integer,
  input_claim_token uuid
)
returns setof public.event_paper_trade_runs
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  attempt_row public.event_paper_broker_attempts%rowtype;
  analysis_row public.event_ai_analyses%rowtype;
  claim_row public.event_paper_trade_event_claims%rowtype;
  recovered public.event_paper_trade_runs%rowtype;
  now_value timestamptz := clock_timestamp();
begin
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

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

  select * into attempt_row
  from public.event_paper_broker_attempts
  where event_id = input_event_id
  for update;
  if not found
     or attempt_row.status <> 'completed'
     or attempt_row.task_id <> input_task_id
     or attempt_row.analysis_id <> input_analysis_id
     or attempt_row.expectation_version <> input_expectation_version
     or attempt_row.order_payload is null then
    return;
  end if;

  select * into analysis_row
  from public.event_ai_analyses
  where id = input_analysis_id;
  if not found
     or analysis_row.event_id <> input_event_id
     or analysis_row.expectation_version <> input_expectation_version then
    raise exception 'paper_broker_recovery_analysis_mismatch';
  end if;

  -- The external/simulated order is already authoritative. Do not rerun the
  -- strategy. Preserve the exact broker order and mark the audit explicitly as
  -- recovered after interrupted terminal persistence.
  select * into recovered
  from public.save_event_paper_trade_result_for_task(
    jsonb_build_object(
      'event_id', input_event_id,
      'expectation_version', input_expectation_version,
      'source_document_id', analysis_row.source_document_id,
      'analysis_id', input_analysis_id,
      'claim_token', input_claim_token,
      'task_id', input_task_id,
      'status', 'paper_executed',
      'message', 'broker order recovered after interrupted paper-run persistence',
      'strategy', null,
      'risk', null,
      'paper_order', attempt_row.order_payload,
      'completed_components', null,
      'confirmation_deadline_at', null,
      'expired_at', null,
      'updated_at', now_value
    )
  )
  limit 1;

  if found then
    return next recovered;
  end if;
end;
$$;

-- Replace the public task-aware claim wrapper so completed broker attempts are
-- reconciled before the claim is returned to orchestration.
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
security definer
set search_path = public, pg_temp
as $$
declare
  claim_row public.event_paper_trade_event_claims%rowtype;
  recovered_run public.event_paper_trade_runs%rowtype;
begin
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));

  select * into claim_row
  from public.claim_event_paper_run_for_task(
    input_event_id,
    input_analysis_id,
    input_task_id,
    input_expectation_version,
    input_claim_token,
    input_lease_seconds
  )
  limit 1;

  if not found then
    return;
  end if;

  if claim_row.terminal_status is null
     and claim_row.task_id = input_task_id
     and claim_row.analysis_id = input_analysis_id
     and claim_row.claim_token = input_claim_token then
    select * into recovered_run
    from public.recover_completed_event_paper_broker_attempt(
      input_event_id,
      input_analysis_id,
      input_task_id,
      input_expectation_version,
      input_claim_token
    )
    limit 1;

    if found then
      select * into claim_row
      from public.event_paper_trade_event_claims
      where event_id = input_event_id;
    end if;
  end if;

  return next claim_row;
end;
$$;

revoke all on function public.begin_event_paper_broker_attempt(text, uuid, uuid, integer, uuid, uuid, integer)
  from public, anon, authenticated;
revoke all on function public.recover_completed_event_paper_broker_attempt(text, uuid, uuid, integer, uuid)
  from public, anon, authenticated;
revoke all on function public.claim_event_paper_run_for_task_v2(text, uuid, uuid, integer, uuid, integer)
  from public, anon, authenticated;
grant execute on function public.begin_event_paper_broker_attempt(text, uuid, uuid, integer, uuid, uuid, integer)
  to service_role;
grant execute on function public.claim_event_paper_run_for_task_v2(text, uuid, uuid, integer, uuid, integer)
  to service_role;
