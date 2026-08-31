-- Serialize the portfolio-snapshot -> Risk -> broker boundary across worker instances.
-- Event leases remain per event; this singleton lease protects the shared PAPER account state.

create table if not exists public.paper_portfolio_execution_lease (
  singleton boolean primary key default true check (singleton),
  lease_token uuid not null,
  lease_expires_at timestamptz not null,
  updated_at timestamptz not null default now()
);

alter table public.paper_portfolio_execution_lease enable row level security;
revoke all on table public.paper_portfolio_execution_lease from public, anon, authenticated, service_role;
grant select on table public.paper_portfolio_execution_lease to service_role;

create or replace function public.claim_paper_portfolio_execution_lease(
  input_lease_token uuid,
  input_lease_seconds integer
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  now_value timestamptz := clock_timestamp();
  changed integer;
begin
  if input_lease_token is null or input_lease_seconds is null or input_lease_seconds < 1 then
    raise exception 'paper_portfolio_lease_identity_invalid';
  end if;

  insert into public.paper_portfolio_execution_lease(singleton, lease_token, lease_expires_at, updated_at)
  values (true, input_lease_token, now_value + make_interval(secs => input_lease_seconds), now_value)
  on conflict (singleton) do update set
    lease_token = excluded.lease_token,
    lease_expires_at = excluded.lease_expires_at,
    updated_at = excluded.updated_at
  where paper_portfolio_execution_lease.lease_token = input_lease_token
     or paper_portfolio_execution_lease.lease_expires_at <= now_value;

  get diagnostics changed = row_count;
  return changed = 1;
end;
$$;

create or replace function public.renew_paper_portfolio_execution_lease(
  input_lease_token uuid,
  input_lease_seconds integer
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  now_value timestamptz := clock_timestamp();
  changed integer;
begin
  if input_lease_token is null or input_lease_seconds is null or input_lease_seconds < 1 then
    raise exception 'paper_portfolio_lease_identity_invalid';
  end if;

  update public.paper_portfolio_execution_lease
  set lease_expires_at = now_value + make_interval(secs => input_lease_seconds),
      updated_at = now_value
  where singleton = true
    and lease_token = input_lease_token
    and lease_expires_at > now_value;

  get diagnostics changed = row_count;
  return changed = 1;
end;
$$;

create or replace function public.release_paper_portfolio_execution_lease(input_lease_token uuid)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  changed integer;
begin
  if input_lease_token is null then
    raise exception 'paper_portfolio_lease_identity_invalid';
  end if;

  delete from public.paper_portfolio_execution_lease
  where singleton = true and lease_token = input_lease_token;
  get diagnostics changed = row_count;
  return changed = 1;
end;
$$;

revoke all on function public.claim_paper_portfolio_execution_lease(uuid, integer) from public, anon, authenticated;
revoke all on function public.renew_paper_portfolio_execution_lease(uuid, integer) from public, anon, authenticated;
revoke all on function public.release_paper_portfolio_execution_lease(uuid) from public, anon, authenticated;
grant execute on function public.claim_paper_portfolio_execution_lease(uuid, integer) to service_role;
grant execute on function public.renew_paper_portfolio_execution_lease(uuid, integer) to service_role;
grant execute on function public.release_paper_portfolio_execution_lease(uuid) to service_role;
