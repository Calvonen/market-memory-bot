-- Bind canonical PAPER execution authority to the durable paper-run audit.
-- Legacy paper runs remain valid with a null task_id; the tracked-event
-- orchestration introduced after canonical trading tasks always uses the
-- task-aware claim RPC below.

alter table public.event_paper_trade_runs
  add column if not exists task_id uuid null
    references public.trading_tasks(id) on delete restrict;

alter table public.event_paper_trade_event_claims
  add column if not exists task_id uuid null
    references public.trading_tasks(id) on delete restrict;

create unique index if not exists event_paper_trade_runs_task_uidx
  on public.event_paper_trade_runs(task_id)
  where task_id is not null;

create index if not exists event_paper_trade_claims_task_idx
  on public.event_paper_trade_event_claims(task_id)
  where task_id is not null;

-- Once a claim is bound to a canonical task, the legacy non-task claim path may
-- not reclaim it. This keeps cancelled/revoked authority from re-entering a
-- broker path through older orchestration.
create or replace function public.claim_event_paper_run(
  input_event_id text,
  input_analysis_id uuid,
  input_claim_token uuid,
  input_lease_seconds integer
)
returns setof public.event_paper_trade_event_claims
language plpgsql
security invoker
set search_path = public
as $$
declare
  terminal_analysis_id uuid;
  terminal_status_value text;
  existing_claim public.event_paper_trade_event_claims%rowtype;
begin
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

  select * into existing_claim
  from public.event_paper_trade_event_claims
  where event_id = input_event_id
  for update;

  if found and existing_claim.task_id is not null then
    raise exception 'paper_run_task_bound_claim_requires_task';
  end if;

  select analysis_id, status
  into terminal_analysis_id, terminal_status_value
  from public.event_paper_trade_runs
  where event_id = input_event_id
    and status in ('expired_no_trade', 'paper_executed')
  limit 1;

  if terminal_analysis_id is not null then
    insert into public.event_paper_trade_event_claims(
      event_id, analysis_id, lease_expires_at, terminal_status, updated_at
    ) values (
      input_event_id, terminal_analysis_id, null, terminal_status_value, clock_timestamp()
    )
    on conflict (event_id) do update set
      analysis_id = excluded.analysis_id,
      lease_expires_at = null,
      terminal_status = excluded.terminal_status,
      updated_at = excluded.updated_at;
  else
    insert into public.event_paper_trade_event_claims(
      event_id, analysis_id, claim_token, claimed_at, lease_expires_at,
      terminal_status, updated_at
    ) values (
      input_event_id,
      input_analysis_id,
      input_claim_token,
      clock_timestamp(),
      clock_timestamp() + make_interval(secs => greatest(input_lease_seconds, 1)),
      null,
      clock_timestamp()
    )
    on conflict (event_id) do update set
      analysis_id = excluded.analysis_id,
      claim_token = excluded.claim_token,
      claimed_at = case
        when event_paper_trade_event_claims.analysis_id = excluded.analysis_id
         and event_paper_trade_event_claims.claim_token = excluded.claim_token
          then event_paper_trade_event_claims.claimed_at
        else excluded.claimed_at
      end,
      lease_expires_at = excluded.lease_expires_at,
      terminal_status = null,
      updated_at = excluded.updated_at
    where event_paper_trade_event_claims.task_id is null
      and (
        (
          event_paper_trade_event_claims.analysis_id = excluded.analysis_id
          and event_paper_trade_event_claims.claim_token = excluded.claim_token
        )
        or event_paper_trade_event_claims.lease_expires_at <= clock_timestamp()
      );
  end if;

  return query
  select * from public.event_paper_trade_event_claims
  where event_id = input_event_id;
end;
$$;

create or replace function public.claim_event_paper_run_for_task(
  input_event_id text,
  input_analysis_id uuid,
  input_task_id uuid,
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
  now_value timestamptz := clock_timestamp();
begin
  if input_task_id is null then
    raise exception 'paper_run_task_missing';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

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

  -- Same task/token is a normal retry and renews the lease.
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

  -- An active lease cannot be displaced, regardless of task identity.
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

  -- Expired legacy claim or an expired claim belonging to a cancelled task may
  -- be transferred to the freshly approved replacement task.
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
begin
  select task_id into claimed_task_id
  from public.event_paper_trade_event_claims
  where event_id = new.event_id
    and analysis_id = new.analysis_id
  limit 1;

  if claimed_task_id is null then
    return new;
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

drop trigger if exists bind_event_paper_run_task_from_claim
  on public.event_paper_trade_runs;
create trigger bind_event_paper_run_task_from_claim
before insert or update on public.event_paper_trade_runs
for each row execute function public.bind_event_paper_run_task_from_claim();

revoke all on function public.claim_event_paper_run_for_task(text, uuid, uuid, uuid, integer)
  from public, anon, authenticated;
grant execute on function public.claim_event_paper_run_for_task(text, uuid, uuid, uuid, integer)
  to service_role;
