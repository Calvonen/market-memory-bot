-- A durable broker attempt represents either a completed order awaiting canonical
-- persistence or an uncertain external side effect. Do not cancel/rebind that
-- authority until the event has a terminal paper-run owner.
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
  source_event text;
  task_row public.trading_tasks%rowtype;
  active_claim boolean;
  unresolved_broker_attempt boolean;
  cancelled public.trading_tasks%rowtype;
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

  perform pg_advisory_xact_lock(hashtextextended(source_event, 1));
  perform pg_advisory_xact_lock(hashtextextended(source_event, 0));

  select * into task_row
  from public.trading_tasks
  where id = input_task_id
  for update;
  if not found then
    raise exception 'trading_task_not_found';
  end if;

  if task_row.state = 'approved' then
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

    select exists (
      select 1
      from public.event_paper_broker_attempts attempt
      where attempt.task_id = input_task_id
        and not exists (
          select 1
          from public.event_paper_trade_runs run
          where run.event_id = attempt.event_id
            and run.status in ('paper_executed', 'expired_no_trade')
        )
    ) into unresolved_broker_attempt;
    if unresolved_broker_attempt then
      raise exception 'trading_task_broker_attempt_unresolved';
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

revoke all on function public.cancel_trading_task(uuid, text)
  from public, anon, authenticated;
grant execute on function public.cancel_trading_task(uuid, text)
  to service_role;
