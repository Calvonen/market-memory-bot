-- Terminal paper-run states are immutable. These functions make stale-writer
-- rejection a database operation rather than a Python read-then-write check.
alter table public.event_paper_trade_runs
  add column if not exists superseded_at timestamptz null,
  add column if not exists superseded_by_analysis_id uuid null;

alter table public.event_paper_trade_runs
  drop constraint if exists event_paper_trade_runs_status_check;
alter table public.event_paper_trade_runs
  add constraint event_paper_trade_runs_status_check
  check (status in ('waiting_confirmation', 'paper_executed', 'expired_no_trade', 'superseded'));

-- Reconcile historical duplicates without deleting their audit payload. The
-- earliest paper execution wins; absent one, the earliest expiry wins. Losing
-- rows retain their strategy/risk/order JSON and point at the winning analysis.
with ranked_terminal as (
  select
    id,
    row_number() over (
      partition by event_id
      order by
        case status when 'paper_executed' then 0 else 1 end,
        updated_at asc,
        first_seen_at asc,
        id asc
    ) as terminal_rank,
    first_value(analysis_id) over (
      partition by event_id
      order by
        case status when 'paper_executed' then 0 else 1 end,
        updated_at asc,
        first_seen_at asc,
        id asc
    ) as winner_analysis_id
  from public.event_paper_trade_runs
  where status in ('expired_no_trade', 'paper_executed')
)
update public.event_paper_trade_runs as run
set
  status = 'superseded',
  superseded_at = coalesce(run.superseded_at, now()),
  superseded_by_analysis_id = ranked_terminal.winner_analysis_id,
  message = run.message || ' [superseded by event terminal owner '
    || ranked_terminal.winner_analysis_id::text || ']'
from ranked_terminal
where run.id = ranked_terminal.id
  and ranked_terminal.terminal_rank > 1;

create unique index if not exists event_paper_trade_runs_one_terminal_per_event_idx
  on public.event_paper_trade_runs(event_id)
  where status in ('expired_no_trade', 'paper_executed');

create table if not exists public.event_paper_trade_event_claims (
  event_id text primary key,
  analysis_id uuid not null,
  claimed_at timestamptz not null default now(),
  lease_expires_at timestamptz null,
  updated_at timestamptz not null default now()
);

alter table public.event_paper_trade_event_claims
  add column if not exists lease_expires_at timestamptz null,
  add column if not exists updated_at timestamptz not null default now();

-- Claims created by an older/partially-applied migration have no trustworthy
-- lease. Treat them as immediately reclaimable unless a terminal run exists.
update public.event_paper_trade_event_claims
set lease_expires_at = coalesce(lease_expires_at, claimed_at)
where lease_expires_at is null;

alter table public.event_paper_trade_event_claims enable row level security;
revoke all on table public.event_paper_trade_event_claims from anon, authenticated;
grant select, insert, update on table public.event_paper_trade_event_claims to service_role;

create or replace function public.save_event_paper_trade_result(input_payload jsonb)
returns setof public.event_paper_trade_runs
language plpgsql
security invoker
set search_path = public
as $$
declare
  effective_event_id text := input_payload->>'event_id';
  effective_analysis_id uuid := (input_payload->>'analysis_id')::uuid;
  effective_status text := input_payload->>'status';
  effective_deadline timestamptz := nullif(input_payload->>'confirmation_deadline_at', '')::timestamptz;
  effective_expired_at timestamptz := nullif(input_payload->>'expired_at', '')::timestamptz;
  terminal_owner public.event_paper_trade_runs%rowtype;
  claim_owner_analysis_id uuid;
begin
  -- All analyses for one event share a transaction-scoped lock. The partial
  -- unique index is the invariant backstop; this lock also lets the losing RPC
  -- return the existing event-level owner instead of raising a unique error.
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

  select analysis_id into claim_owner_analysis_id
  from public.event_paper_trade_event_claims
  where event_id = effective_event_id;
  if claim_owner_analysis_id is not null
     and claim_owner_analysis_id <> effective_analysis_id then
    return query
    select * from public.event_paper_trade_runs
    where analysis_id = effective_analysis_id
    limit 1;
    return;
  end if;

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
    effective_event_id,
    (input_payload->>'expectation_version')::integer,
    nullif(input_payload->>'source_document_id', '')::uuid,
    effective_analysis_id,
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

drop function if exists public.claim_event_paper_run(text, uuid);
create or replace function public.claim_event_paper_run(
  input_event_id text,
  input_analysis_id uuid,
  input_lease_seconds integer
)
returns setof public.event_paper_trade_event_claims
language plpgsql
security invoker
set search_path = public
as $$
declare
  terminal_analysis_id uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

  select analysis_id into terminal_analysis_id
  from public.event_paper_trade_runs
  where event_id = input_event_id
    and status in ('expired_no_trade', 'paper_executed')
  limit 1;

  if terminal_analysis_id is not null then
    insert into public.event_paper_trade_event_claims(
      event_id, analysis_id, lease_expires_at, updated_at
    ) values (input_event_id, terminal_analysis_id, null, clock_timestamp())
    on conflict (event_id) do update set
      analysis_id = excluded.analysis_id,
      lease_expires_at = null,
      updated_at = excluded.updated_at;
  else
    insert into public.event_paper_trade_event_claims(
      event_id, analysis_id, claimed_at, lease_expires_at, updated_at
    ) values (
      input_event_id,
      input_analysis_id,
      clock_timestamp(),
      clock_timestamp() + make_interval(secs => greatest(input_lease_seconds, 1)),
      clock_timestamp()
    )
    on conflict (event_id) do update set
      analysis_id = excluded.analysis_id,
      claimed_at = case
        when event_paper_trade_event_claims.analysis_id = excluded.analysis_id
          then event_paper_trade_event_claims.claimed_at
        else excluded.claimed_at
      end,
      lease_expires_at = excluded.lease_expires_at,
      updated_at = excluded.updated_at
    where event_paper_trade_event_claims.analysis_id = excluded.analysis_id
       or event_paper_trade_event_claims.lease_expires_at <= clock_timestamp();
  end if;

  return query
  select * from public.event_paper_trade_event_claims
  where event_id = input_event_id;
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
language plpgsql
security invoker
set search_path = public
as $$
declare
  terminal_owner public.event_paper_trade_runs%rowtype;
  claim_owner_analysis_id uuid;
  claim_lease_expires_at timestamptz;
  expired_run public.event_paper_trade_runs%rowtype;
begin
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));
  select * into terminal_owner
  from public.event_paper_trade_runs
  where event_id = input_event_id
    and status in ('expired_no_trade', 'paper_executed')
  limit 1;
  if found then
    return next terminal_owner;
    return;
  end if;

  select analysis_id, lease_expires_at
  into claim_owner_analysis_id, claim_lease_expires_at
  from public.event_paper_trade_event_claims
  where event_id = input_event_id;
  if claim_owner_analysis_id is not null
     and claim_owner_analysis_id <> input_analysis_id then
    if claim_lease_expires_at is null
       or claim_lease_expires_at > clock_timestamp() then
      return query
      select * from public.event_paper_trade_runs
      where analysis_id = input_analysis_id
      limit 1;
      return;
    end if;

    -- The lease is stale. Transfer it under the same event advisory lock used
    -- by the expiry transition; no active owner can be displaced.
    update public.event_paper_trade_event_claims
    set
      analysis_id = input_analysis_id,
      claimed_at = clock_timestamp(),
      lease_expires_at = input_expired_at,
      updated_at = clock_timestamp()
    where event_id = input_event_id
      and analysis_id = claim_owner_analysis_id
      and lease_expires_at <= clock_timestamp();
  end if;

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
  returning event_paper_trade_runs.* into expired_run;

  if found then
    update public.event_paper_trade_event_claims
    set
      analysis_id = input_analysis_id,
      lease_expires_at = null,
      updated_at = clock_timestamp()
    where event_id = input_event_id;
    return next expired_run;
  end if;
  return;
end;
$$;

create or replace function public.get_event_paper_trade_state(input_event_id text)
returns setof public.event_paper_trade_runs
language sql
security invoker
set search_path = public
as $$
  select *
  from public.event_paper_trade_runs
  where event_id = input_event_id
    and status <> 'superseded'
  order by
    case when status in ('expired_no_trade', 'paper_executed') then 0 else 1 end,
    updated_at desc,
    id desc
  limit 1;
$$;

revoke all on function public.save_event_paper_trade_result(jsonb) from public;
revoke all on function public.expire_event_paper_trade_run(text, integer, uuid, uuid, timestamptz, timestamptz) from public;
revoke all on function public.claim_event_paper_run(text, uuid, integer) from public;
revoke all on function public.get_event_paper_trade_state(text) from public;
grant execute on function public.save_event_paper_trade_result(jsonb) to service_role;
grant execute on function public.expire_event_paper_trade_run(text, integer, uuid, uuid, timestamptz, timestamptz) to service_role;
grant execute on function public.claim_event_paper_run(text, uuid, integer) to service_role;
grant execute on function public.get_event_paper_trade_state(text) to service_role;
