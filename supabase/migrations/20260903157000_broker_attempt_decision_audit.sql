-- Persist the exact Strategy/Risk authorization with the durable broker attempt
-- before broker I/O, so crash recovery never produces an order without its
-- original decision audit.

alter table public.event_paper_broker_attempts
  add column if not exists strategy_payload jsonb null,
  add column if not exists risk_payload jsonb null;

alter table public.event_paper_broker_attempts
  drop constraint if exists event_paper_broker_attempts_status_audit_check;
alter table public.event_paper_broker_attempts
  add constraint event_paper_broker_attempts_status_audit_check check (
    strategy_payload is not null
    and risk_payload is not null
    and (
      (status = 'started' and order_payload is null and completed_at is null)
      or (status = 'completed' and order_payload is not null and completed_at is not null)
    )
  ) not valid;

-- Existing rows can predate the decision-audit columns. New broker entry uses
-- only this overload, which requires both payloads before reserving execution.
create or replace function public.begin_event_paper_broker_attempt(
  input_event_id text,
  input_analysis_id uuid,
  input_task_id uuid,
  input_expectation_version integer,
  input_claim_token uuid,
  input_execution_token uuid,
  input_lease_seconds integer,
  input_strategy_payload jsonb,
  input_risk_payload jsonb
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
     or input_lease_seconds < 1
     or input_strategy_payload is null
     or input_strategy_payload = 'null'::jsonb
     or input_risk_payload is null
     or input_risk_payload = 'null'::jsonb then
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
    execution_token, status, strategy_payload, risk_payload, started_at
  ) values (
    input_task_id, input_event_id, input_analysis_id, input_expectation_version,
    input_claim_token, input_execution_token, 'started', input_strategy_payload,
    input_risk_payload, now_value
  )
  on conflict do nothing;

  select * into attempt_row
  from public.event_paper_broker_attempts
  where event_id = input_event_id
  for update;
  if not found then
    raise exception 'paper_broker_attempt_reservation_missing';
  end if;

  if attempt_row.task_id <> input_task_id
     or attempt_row.analysis_id <> input_analysis_id
     or attempt_row.expectation_version <> input_expectation_version then
    raise exception 'paper_broker_attempt_identity_conflict';
  end if;

  if attempt_row.status = 'completed' then
    if attempt_row.strategy_payload is null or attempt_row.risk_payload is null then
      raise exception 'paper_broker_attempt_completed_audit_missing';
    end if;
    return query select false, attempt_row.status, attempt_row.order_payload;
    return;
  end if;

  if attempt_row.claim_token <> input_claim_token
     or attempt_row.execution_token <> input_execution_token then
    return query select false, attempt_row.status, null::jsonb;
    return;
  end if;

  -- The same execution token must replay the exact decision audit.
  if attempt_row.strategy_payload <> input_strategy_payload
     or attempt_row.risk_payload <> input_risk_payload then
    raise exception 'paper_broker_attempt_decision_audit_conflict';
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
     or attempt_row.order_payload is null
     or attempt_row.strategy_payload is null
     or attempt_row.risk_payload is null then
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
      'strategy', attempt_row.strategy_payload,
      'risk', attempt_row.risk_payload,
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

-- Remove runtime access to the older begin overloads: all new broker entry must
-- persist Strategy/Risk before I/O.
revoke execute on function public.begin_event_paper_broker_attempt(text, uuid, uuid, integer, uuid, uuid, integer)
  from service_role;
revoke all on function public.begin_event_paper_broker_attempt(text, uuid, uuid, integer, uuid, uuid, integer, jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function public.begin_event_paper_broker_attempt(text, uuid, uuid, integer, uuid, uuid, integer, jsonb, jsonb)
  to service_role;
