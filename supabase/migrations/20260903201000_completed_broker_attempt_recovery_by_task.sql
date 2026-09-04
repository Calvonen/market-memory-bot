-- Recovery-only path for broker attempts that were already durably completed.
-- This path never creates a broker attempt and therefore must not depend on the
-- current expectation version, current market data, or current task approval
-- state. The completed broker-attempt row is the immutable execution authority.

create or replace function public.bind_event_paper_run_task_from_claim()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  claimed_task_id uuid;
  claimed_task public.trading_tasks%rowtype;
  previous_task public.trading_tasks%rowtype;
  completed_attempt public.event_paper_broker_attempts%rowtype;
begin
  select task_id into claimed_task_id
  from public.event_paper_trade_event_claims
  where event_id = new.event_id
    and analysis_id = new.analysis_id
  limit 1;

  if claimed_task_id is null then
    return new;
  end if;

  -- A broker order that is already durably completed must remain recoverable
  -- even if the task was later cancelled or the current expectation advanced.
  -- Require the exact persisted execution lineage and exact broker order payload;
  -- this exception cannot authorize a new broker call.
  if new.status = 'paper_executed' then
    select * into completed_attempt
    from public.event_paper_broker_attempts
    where event_id = new.event_id
      and task_id = claimed_task_id
      and analysis_id = new.analysis_id
      and expectation_version = new.expectation_version
      and status = 'completed'
      and order_payload = new.paper_order
    limit 1;

    if found then
      new.task_id := claimed_task_id;
      return new;
    end if;
  end if;

  select * into claimed_task
  from public.trading_tasks
  where id = claimed_task_id
  for share;

  if not found
     or claimed_task.state <> 'approved'
     or claimed_task.mode <> 'PAPER'
     or claimed_task.source_event_id <> new.event_id then
    raise exception 'paper_run_task_authority_revoked';
  end if;

  if new.task_id is not null and new.task_id <> claimed_task_id then
    raise exception 'paper_run_task_audit_conflict';
  end if;

  if tg_op = 'UPDATE'
     and old.task_id is not null
     and old.task_id <> claimed_task_id then
    if old.status in ('expired_no_trade', 'paper_executed') then
      raise exception 'paper_run_task_replacement_conflict';
    end if;

    select * into previous_task
    from public.trading_tasks
    where id = old.task_id
    for share;

    if not found or previous_task.state <> 'cancelled' then
      raise exception 'paper_run_task_replacement_conflict';
    end if;
  end if;

  new.task_id := claimed_task_id;
  return new;
end;
$$;

create or replace function public.recover_completed_event_paper_broker_attempt_for_task(
  input_event_id text,
  input_task_id uuid
)
returns setof public.event_paper_trade_runs
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  attempt_row public.event_paper_broker_attempts%rowtype;
  analysis_row public.event_ai_analyses%rowtype;
  task_row public.trading_tasks%rowtype;
  terminal_run public.event_paper_trade_runs%rowtype;
  recovered public.event_paper_trade_runs%rowtype;
  now_value timestamptz := clock_timestamp();
begin
  if input_event_id is null or btrim(input_event_id) = '' or input_task_id is null then
    raise exception 'paper_broker_recovery_identity_invalid';
  end if;

  -- Keep the same event lock order used by broker-attempt reservation.
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

  select * into terminal_run
  from public.event_paper_trade_runs
  where event_id = input_event_id
    and status in ('expired_no_trade', 'paper_executed')
  limit 1;
  if found then
    return next terminal_run;
    return;
  end if;

  select * into attempt_row
  from public.event_paper_broker_attempts
  where event_id = input_event_id
    and task_id = input_task_id
    and status = 'completed'
  for update;

  if not found then
    return;
  end if;
  if attempt_row.order_payload is null then
    raise exception 'paper_broker_recovery_order_missing';
  end if;

  select * into task_row
  from public.trading_tasks
  where id = attempt_row.task_id
  for share;
  if not found
     or task_row.mode <> 'PAPER'
     or task_row.source_event_id <> input_event_id then
    raise exception 'paper_broker_recovery_task_mismatch';
  end if;

  select * into analysis_row
  from public.event_ai_analyses
  where id = attempt_row.analysis_id;
  if not found
     or analysis_row.event_id <> input_event_id
     or analysis_row.expectation_version <> attempt_row.expectation_version then
    raise exception 'paper_broker_recovery_analysis_mismatch';
  end if;

  -- Re-establish only the persistence lease for the already-completed attempt.
  -- This does not grant new execution authority and does not call the broker.
  insert into public.event_paper_trade_event_claims(
    event_id, analysis_id, task_id, claim_token, claimed_at,
    lease_expires_at, terminal_status, updated_at
  ) values (
    input_event_id,
    attempt_row.analysis_id,
    attempt_row.task_id,
    attempt_row.claim_token,
    now_value,
    now_value + interval '60 seconds',
    null,
    now_value
  )
  on conflict (event_id) do update set
    analysis_id = excluded.analysis_id,
    task_id = excluded.task_id,
    claim_token = excluded.claim_token,
    claimed_at = excluded.claimed_at,
    lease_expires_at = excluded.lease_expires_at,
    terminal_status = null,
    updated_at = excluded.updated_at;

  select * into recovered
  from public.save_event_paper_trade_result(
    jsonb_build_object(
      'event_id', input_event_id,
      'expectation_version', attempt_row.expectation_version,
      'source_document_id', analysis_row.source_document_id,
      'analysis_id', attempt_row.analysis_id,
      'claim_token', attempt_row.claim_token,
      'task_id', attempt_row.task_id,
      'status', 'paper_executed',
      'message', 'broker order recovered from durable completed attempt',
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
    update public.event_paper_trade_event_claims
    set
      analysis_id = attempt_row.analysis_id,
      task_id = attempt_row.task_id,
      claim_token = attempt_row.claim_token,
      lease_expires_at = null,
      terminal_status = recovered.status,
      updated_at = clock_timestamp()
    where event_id = input_event_id;
    return next recovered;
  end if;
end;
$$;

revoke all on function public.recover_completed_event_paper_broker_attempt_for_task(text, uuid)
  from public, anon, authenticated;
grant execute on function public.recover_completed_event_paper_broker_attempt_for_task(text, uuid)
  to service_role;

-- Preserve the existing readiness RPC shape. The third boolean remains the
-- runtime dependency gate for evidence execution and now also requires the
-- recovery-only primitive used by the dispatcher.
create or replace function public.verify_market_open_runtime_schema()
returns table (
  market_open_shell_function_exists boolean,
  market_open_shell_trigger_exists boolean,
  freeze_market_open_evidence_function_exists boolean
)
language sql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select
    to_regprocedure('public.ensure_market_open_strategy_shell(uuid)') is not null,
    exists (
      select 1
      from pg_catalog.pg_trigger t
      join pg_catalog.pg_class c on c.oid = t.tgrelid
      join pg_catalog.pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public'
        and c.relname = 'tracked_market_events'
        and t.tgname = 'tracked_market_events_market_open_shell_after_date_write'
        and not t.tgisinternal
    ),
    to_regprocedure('public.freeze_market_open_evidence(uuid,integer,text,jsonb)') is not null
      and to_regprocedure('public.recover_completed_event_paper_broker_attempt_for_task(text,uuid)') is not null;
$$;

revoke all on function public.verify_market_open_runtime_schema()
  from public, anon, authenticated;
grant execute on function public.verify_market_open_runtime_schema()
  to service_role;
