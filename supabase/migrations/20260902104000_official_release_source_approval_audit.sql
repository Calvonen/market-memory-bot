begin;

create table public.event_official_release_source_audit (
  id bigint generated always as identity primary key,
  event_id text not null references public.market_events(event_id) on delete cascade,
  action text not null check (action in ('set', 'clear')),
  actor text not null check (length(btrim(actor)) > 0),
  version integer not null check (version > 0),
  source_kind text,
  source_url text,
  source_title text,
  created_at timestamptz not null default clock_timestamp(),
  constraint event_official_release_source_audit_shape_check check (
    (action = 'set' and source_kind in ('direct_url', 'results_page') and source_url is not null)
    or
    (action = 'clear' and source_kind is null and source_url is null and source_title is null)
  )
);

comment on table public.event_official_release_source_audit is
  'Append-only audit trail for privileged official release source approvals and clears.';

alter table public.event_official_release_source_audit enable row level security;
revoke all on table public.event_official_release_source_audit from public, anon, authenticated, service_role;
grant select on table public.event_official_release_source_audit to service_role;

create function public.set_event_official_release_source_approved(
  input_event_id text,
  input_source_kind text,
  input_source_url text,
  input_source_title text,
  input_expected_version integer,
  input_actor text
)
returns table (
  out_event_id text,
  out_source_kind text,
  out_source_url text,
  out_source_title text,
  out_version integer,
  out_created_at timestamptz,
  out_updated_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  approved record;
  canonical_actor text := btrim(input_actor);
begin
  if canonical_actor is null or canonical_actor = '' then
    raise exception 'invalid_actor' using errcode = '22023';
  end if;

  select * into approved
  from public.set_event_official_release_source(
    input_event_id,
    input_source_kind,
    input_source_url,
    input_source_title,
    input_expected_version
  );

  insert into public.event_official_release_source_audit (
    event_id, action, actor, version, source_kind, source_url, source_title
  ) values (
    approved.out_event_id,
    'set',
    canonical_actor,
    approved.out_version,
    approved.out_source_kind,
    approved.out_source_url,
    approved.out_source_title
  );

  return query select
    approved.out_event_id,
    approved.out_source_kind,
    approved.out_source_url,
    approved.out_source_title,
    approved.out_version,
    approved.out_created_at,
    approved.out_updated_at;
end;
$$;

create function public.clear_event_official_release_source_approved(
  input_event_id text,
  input_expected_version integer,
  input_actor text
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  new_version integer;
  canonical_actor text := btrim(input_actor);
begin
  if canonical_actor is null or canonical_actor = '' then
    raise exception 'invalid_actor' using errcode = '22023';
  end if;

  new_version := public.clear_event_official_release_source(
    input_event_id,
    input_expected_version
  );

  insert into public.event_official_release_source_audit (
    event_id, action, actor, version, source_kind, source_url, source_title
  ) values (
    input_event_id,
    'clear',
    canonical_actor,
    new_version,
    null,
    null,
    null
  );

  return new_version;
end;
$$;

revoke all on function public.set_event_official_release_source(text, text, text, text, integer)
  from service_role;
revoke all on function public.clear_event_official_release_source(text, integer)
  from service_role;

revoke all on function public.set_event_official_release_source_approved(text, text, text, text, integer, text)
  from public, anon, authenticated;
grant execute on function public.set_event_official_release_source_approved(text, text, text, text, integer, text)
  to service_role;

revoke all on function public.clear_event_official_release_source_approved(text, integer, text)
  from public, anon, authenticated;
grant execute on function public.clear_event_official_release_source_approved(text, integer, text)
  to service_role;

-- The 1030 migration exposed a three-column verifier. Drop it before changing
-- the return shape so deploy tooling can distinguish the audited contract from
-- the older unaudited schema instead of accepting three legacy true values.
drop function public.verify_official_release_source_schema();

create function public.verify_official_release_source_schema()
returns table (
  event_official_release_sources_table_exists boolean,
  set_event_official_release_source_function_exists boolean,
  clear_event_official_release_source_function_exists boolean,
  official_release_source_schema_version integer
)
language sql
security invoker
set search_path = public
as $$
  select
    to_regclass('public.event_official_release_sources') is not null
      and to_regclass('public.event_official_release_source_audit') is not null,
    to_regprocedure(
      'public.set_event_official_release_source(text, text, text, text, integer)'
    ) is not null
      and to_regprocedure(
        'public.set_event_official_release_source_approved(text, text, text, text, integer, text)'
      ) is not null,
    to_regprocedure(
      'public.clear_event_official_release_source(text, integer)'
    ) is not null
      and to_regprocedure(
        'public.clear_event_official_release_source_approved(text, integer, text)'
      ) is not null,
    2;
$$;

revoke all on function public.verify_official_release_source_schema() from public, anon, authenticated;
grant execute on function public.verify_official_release_source_schema() to service_role;

commit;
