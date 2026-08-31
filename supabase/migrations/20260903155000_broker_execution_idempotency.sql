-- Make the task-aware claim wrapper executable without exposing its delegated
-- primitive directly to service_role, and make one broker execution attempt
-- durable per canonical trading task.

alter function public.claim_event_paper_run_for_task_v2(text, uuid, uuid, integer, uuid, integer)
  security definer;
alter function public.claim_event_paper_run_for_task_v2(text, uuid, uuid, integer, uuid, integer)
  set search_path = public, pg_temp;

create table if not exists public.event_paper_broker_attempts (
  task_id uuid primary key references public.trading_tasks(id) on delete restrict,
  event_id text not null,
  analysis_id uuid not null,
  expectation_version integer not null check (expectation_version > 0),
  claim_token uuid not null,
  execution_token uuid not null,
  status text not null check (status in ('started', 'completed')),
  order_payload jsonb null,
  started_at timestamptz not null default now(),
  completed_at timestamptz null,
  check (
    (status = 'started' and order_payload is null and completed_at is null)
    or (status = 'completed' and order_payload is not null and completed_at is not null)
  )
);

alter table public.event_paper_broker_attempts enable row level security;
revoke all on table public.event_paper_broker_attempts
  from public, anon, authenticated, service_role;
grant select on table public.event_paper_broker_attempts to service_role;

create or replace function public.begin_event_paper_broker_attempt(
  input_event_id text,
  input_analysis_id uuid,
  input_task_id uuid,
  input_expectation_version integer,
  input_claim_token uuid,
  input_execution_token uuid
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
     or input_execution_token is null then
    raise exception 'paper_broker_attempt_identity_invalid';
  end if;

  -- Lock order matches the canonical task-aware claim path.
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
  on conflict (task_id) do nothing;

  select * into attempt_row
  from public.event_paper_broker_attempts
  where task_id = input_task_id
  for update;

  if attempt_row.event_id <> input_event_id
     or attempt_row.analysis_id <> input_analysis_id
     or attempt_row.expectation_version <> input_expectation_version
     or attempt_row.claim_token <> input_claim_token then
    raise exception 'paper_broker_attempt_identity_conflict';
  end if;

  if attempt_row.status = 'completed' then
    return query select false, attempt_row.status, attempt_row.order_payload;
    return;
  end if;

  if attempt_row.execution_token <> input_execution_token then
    -- A prior process may already have reached the broker. Its outcome is
    -- intentionally treated as uncertain until reconciled; never submit again.
    return query select false, attempt_row.status, null::jsonb;
    return;
  end if;

  -- Renew the claim only for the execution token that owns this one durable
  -- attempt. This closes the gap between reservation and the immediate broker call.
  update public.event_paper_trade_event_claims
  set lease_expires_at = greatest(lease_expires_at, now_value + interval '120 seconds'),
      updated_at = now_value
  where event_id = input_event_id
    and task_id = input_task_id
    and analysis_id = input_analysis_id
    and claim_token = input_claim_token;

  return query select true, attempt_row.status, null::jsonb;
end;
$$;

create or replace function public.complete_event_paper_broker_attempt(
  input_task_id uuid,
  input_execution_token uuid,
  input_order_payload jsonb
)
returns setof public.event_paper_broker_attempts
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  attempt_row public.event_paper_broker_attempts%rowtype;
begin
  if input_task_id is null or input_execution_token is null
     or input_order_payload is null or input_order_payload = 'null'::jsonb then
    raise exception 'paper_broker_attempt_completion_invalid';
  end if;

  select * into attempt_row
  from public.event_paper_broker_attempts
  where task_id = input_task_id
  for update;
  if not found then
    raise exception 'paper_broker_attempt_not_found';
  end if;
  if attempt_row.execution_token <> input_execution_token then
    raise exception 'paper_broker_attempt_execution_token_mismatch';
  end if;

  if attempt_row.status = 'completed' then
    if attempt_row.order_payload <> input_order_payload then
      raise exception 'paper_broker_attempt_order_conflict';
    end if;
    return next attempt_row;
    return;
  end if;

  update public.event_paper_broker_attempts
  set status = 'completed',
      order_payload = input_order_payload,
      completed_at = clock_timestamp()
  where task_id = input_task_id
  returning * into attempt_row;

  return next attempt_row;
end;
$$;

revoke all on function public.begin_event_paper_broker_attempt(text, uuid, uuid, integer, uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.complete_event_paper_broker_attempt(uuid, uuid, jsonb)
  from public, anon, authenticated;
grant execute on function public.begin_event_paper_broker_attempt(text, uuid, uuid, integer, uuid, uuid)
  to service_role;
grant execute on function public.complete_event_paper_broker_attempt(uuid, uuid, jsonb)
  to service_role;
