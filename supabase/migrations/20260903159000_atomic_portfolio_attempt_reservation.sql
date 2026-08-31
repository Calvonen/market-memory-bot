-- Atomically validate/renew the singleton PAPER portfolio lease and reserve the
-- canonical event broker attempt in one transaction. This closes the gap where
-- another worker could acquire the account after a standalone renewal but before
-- the event-scoped broker attempt was reserved.

create or replace function public.begin_event_paper_broker_attempt_with_portfolio_lease(
  input_event_id text,
  input_analysis_id uuid,
  input_task_id uuid,
  input_expectation_version integer,
  input_claim_token uuid,
  input_execution_token uuid,
  input_lease_seconds integer,
  input_strategy_payload jsonb,
  input_risk_payload jsonb,
  input_portfolio_lease_token uuid,
  input_portfolio_lease_seconds integer
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
  now_value timestamptz := clock_timestamp();
  portfolio_row public.paper_portfolio_execution_lease%rowtype;
begin
  if input_portfolio_lease_token is null
     or input_portfolio_lease_seconds is null
     or input_portfolio_lease_seconds < 1 then
    raise exception 'paper_portfolio_execution_lease_identity_invalid';
  end if;

  -- Serialize all snapshot->broker authority through the singleton account row.
  select * into portfolio_row
  from public.paper_portfolio_execution_lease
  where id = 1
  for update;

  if not found
     or portfolio_row.lease_token <> input_portfolio_lease_token
     or portfolio_row.lease_expires_at is null
     or portfolio_row.lease_expires_at <= now_value then
    raise exception 'paper_portfolio_execution_lease_not_owned';
  end if;

  update public.paper_portfolio_execution_lease
  set lease_expires_at = now_value + make_interval(secs => greatest(input_portfolio_lease_seconds, 1)),
      updated_at = now_value
  where id = 1
    and lease_token = input_portfolio_lease_token;

  -- The delegated function runs inside this same transaction, while the locked
  -- singleton row prevents another worker from claiming the portfolio.
  return query
  select * from public.begin_event_paper_broker_attempt(
    input_event_id,
    input_analysis_id,
    input_task_id,
    input_expectation_version,
    input_claim_token,
    input_execution_token,
    input_lease_seconds,
    input_strategy_payload,
    input_risk_payload
  );
end;
$$;

revoke all on function public.begin_event_paper_broker_attempt_with_portfolio_lease(
  text, uuid, uuid, integer, uuid, uuid, integer, jsonb, jsonb, uuid, integer
) from public, anon, authenticated;
grant execute on function public.begin_event_paper_broker_attempt_with_portfolio_lease(
  text, uuid, uuid, integer, uuid, uuid, integer, jsonb, jsonb, uuid, integer
) to service_role;
