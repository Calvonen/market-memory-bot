-- Terminal paper-run states are immutable. These functions make stale-writer
-- rejection a database operation rather than a Python read-then-write check.
create or replace function public.save_event_paper_trade_result(input_payload jsonb)
returns setof public.event_paper_trade_runs
language plpgsql
security invoker
set search_path = public
as $$
declare
  effective_status text := input_payload->>'status';
  effective_deadline timestamptz := nullif(input_payload->>'confirmation_deadline_at', '')::timestamptz;
  effective_expired_at timestamptz := nullif(input_payload->>'expired_at', '')::timestamptz;
begin
  -- A runner that started before the deadline but finishes afterwards loses
  -- atomically at persistence time and cannot publish a paper order.
  if effective_status in ('waiting_confirmation', 'paper_executed')
     and effective_deadline is not null
     and clock_timestamp() >= effective_deadline then
    effective_status := 'expired_no_trade';
    effective_expired_at := coalesce(effective_expired_at, clock_timestamp());
  end if;

  return query
  insert into public.event_paper_trade_runs (
    event_id, expectation_version, source_document_id, analysis_id,
    status, message, strategy, risk, paper_order, completed_components,
    confirmation_deadline_at, expired_at, updated_at
  ) values (
    input_payload->>'event_id',
    (input_payload->>'expectation_version')::integer,
    nullif(input_payload->>'source_document_id', '')::uuid,
    (input_payload->>'analysis_id')::uuid,
    effective_status,
    case when effective_status = 'expired_no_trade'
      then 'confirmation window expired without a trade'
      else input_payload->>'message' end,
    case when effective_status = 'expired_no_trade' then null
      else nullif(input_payload->'strategy', 'null'::jsonb) end,
    case when effective_status = 'expired_no_trade' then null
      else nullif(input_payload->'risk', 'null'::jsonb) end,
    case when effective_status = 'expired_no_trade' then null
      else nullif(input_payload->'paper_order', 'null'::jsonb) end,
    nullif(input_payload->'completed_components', 'null'::jsonb),
    effective_deadline,
    effective_expired_at,
    coalesce(nullif(input_payload->>'updated_at', '')::timestamptz, clock_timestamp())
  )
  on conflict (analysis_id) do update set
    status = case
      when coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at) is not null
       and clock_timestamp() >= coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at)
      then 'expired_no_trade'
      else excluded.status
    end,
    message = case
      when coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at) is not null
       and clock_timestamp() >= coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at)
      then 'confirmation window expired without a trade'
      else excluded.message
    end,
    strategy = case
      when coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at) is not null
       and clock_timestamp() >= coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at)
      then null else excluded.strategy end,
    risk = case
      when coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at) is not null
       and clock_timestamp() >= coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at)
      then null else excluded.risk end,
    paper_order = case
      when coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at) is not null
       and clock_timestamp() >= coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at)
      then null else excluded.paper_order end,
    completed_components = excluded.completed_components,
    confirmation_deadline_at = coalesce(
      event_paper_trade_runs.confirmation_deadline_at,
      excluded.confirmation_deadline_at
    ),
    expired_at = case
      when coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at) is not null
       and clock_timestamp() >= coalesce(event_paper_trade_runs.confirmation_deadline_at, excluded.confirmation_deadline_at)
      then coalesce(excluded.expired_at, clock_timestamp())
      else excluded.expired_at
    end,
    updated_at = excluded.updated_at
  where event_paper_trade_runs.status not in ('expired_no_trade', 'paper_executed')
  returning event_paper_trade_runs.*;
end;
$$;

create or replace function public.expire_event_paper_trade_run(
  input_event_id text,
  input_expectation_version integer,
  input_source_document_id uuid,
  input_analysis_id uuid,
  input_confirmation_deadline_at timestamptz,
  input_expired_at timestamptz
)
returns setof public.event_paper_trade_runs
language sql
security invoker
set search_path = public
as $$
  insert into public.event_paper_trade_runs (
    event_id, expectation_version, source_document_id, analysis_id,
    status, message, confirmation_deadline_at, expired_at, updated_at
  ) values (
    input_event_id, input_expectation_version, input_source_document_id, input_analysis_id,
    'expired_no_trade', 'confirmation window expired without a trade',
    input_confirmation_deadline_at, input_expired_at, input_expired_at
  )
  on conflict (analysis_id) do update set
    status = 'expired_no_trade',
    message = 'confirmation window expired without a trade',
    confirmation_deadline_at = coalesce(
      event_paper_trade_runs.confirmation_deadline_at,
      excluded.confirmation_deadline_at
    ),
    expired_at = excluded.expired_at,
    updated_at = excluded.updated_at
  where event_paper_trade_runs.status = 'waiting_confirmation'
  returning event_paper_trade_runs.*;
$$;

revoke all on function public.save_event_paper_trade_result(jsonb) from public;
revoke all on function public.expire_event_paper_trade_run(text, integer, uuid, uuid, timestamptz, timestamptz) from public;
grant execute on function public.save_event_paper_trade_result(jsonb) to service_role;
grant execute on function public.expire_event_paper_trade_run(text, integer, uuid, uuid, timestamptz, timestamptz) to service_role;
