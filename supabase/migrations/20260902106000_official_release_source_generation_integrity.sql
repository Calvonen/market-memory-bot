begin;

-- Stabilize event/source identities while repairing any active row that could be
-- mistaken for an older audited generation. The previous migration already
-- revoked legacy writers; these locks drain concurrent source mutations while
-- this repair establishes the stronger latest-audit invariant.
lock table public.market_events in share mode;
lock table public.event_official_release_sources in access exclusive mode;

create temporary table official_release_source_stale_generation_repair
on commit drop
as
with latest_audit as (
  select distinct on (event_id)
    event_id,
    action,
    version,
    source_kind,
    source_url,
    source_title
  from public.event_official_release_source_audit
  order by event_id, id desc
)
select
  source.event_id,
  source.version,
  source.source_kind,
  source.source_url,
  source.source_title
from public.event_official_release_sources source
left join latest_audit audit
  on audit.event_id = source.event_id
where source.is_active
  and not coalesce(
    audit.action = 'set'
    and audit.version = source.version
    and audit.source_kind is not distinct from source.source_kind
    and audit.source_url is not distinct from source.source_url
    and audit.source_title is not distinct from source.source_title,
    false
  );

-- Preserve the unexpected active row as an explicit migration observation,
-- then advance it to a tombstone. These rows remain untrusted until a fresh
-- audited approval is written after this migration.
insert into public.event_official_release_source_audit (
  event_id, action, actor, version, source_kind, source_url, source_title
)
select
  event_id,
  'set',
  'migration:stale-generation-source',
  version,
  source_kind,
  source_url,
  source_title
from official_release_source_stale_generation_repair;

with invalidated as (
  update public.event_official_release_sources source
  set
    source_kind = null,
    source_url = null,
    source_title = null,
    is_active = false,
    version = source.version + 1,
    updated_at = clock_timestamp()
  from official_release_source_stale_generation_repair repair
  where source.event_id = repair.event_id
    and source.version = repair.version
  returning source.event_id, source.version
)
insert into public.event_official_release_source_audit (
  event_id, action, actor, version, source_kind, source_url, source_title
)
select
  event_id,
  'clear',
  'migration:stale-generation-invalidation',
  version,
  null,
  null,
  null
from invalidated;

-- Canonical runtime read: only the latest audit action for the event may
-- authorize the current active source generation. A historical matching `set`
-- followed by a later `clear` can therefore never reactivate an old generation.
create or replace function public.get_audited_official_release_source_state(
  input_event_id text
)
returns table (
  out_event_id text,
  out_source_kind text,
  out_source_url text,
  out_source_title text,
  out_is_active boolean,
  out_version integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  source_row public.event_official_release_sources%rowtype;
  latest_audit public.event_official_release_source_audit%rowtype;
  audited_active boolean := false;
begin
  select * into source_row
  from public.event_official_release_sources
  where event_id = input_event_id;

  if not found then
    return query select input_event_id, null::text, null::text, null::text, false, 0;
    return;
  end if;

  if source_row.is_active then
    select * into latest_audit
    from public.event_official_release_source_audit
    where event_id = source_row.event_id
    order by id desc
    limit 1;

    audited_active := found
      and latest_audit.action = 'set'
      and latest_audit.version = source_row.version
      and latest_audit.source_kind is not distinct from source_row.source_kind
      and latest_audit.source_url is not distinct from source_row.source_url
      and latest_audit.source_title is not distinct from source_row.source_title;
  end if;

  if source_row.is_active and audited_active then
    return query select
      source_row.event_id,
      source_row.source_kind,
      source_row.source_url,
      source_row.source_title,
      true,
      source_row.version;
  else
    return query select
      source_row.event_id,
      null::text,
      null::text,
      null::text,
      false,
      source_row.version;
  end if;
end;
$$;

revoke all on function public.get_audited_official_release_source_state(text)
  from public, anon, authenticated;
grant execute on function public.get_audited_official_release_source_state(text)
  to service_role;

-- Clear must confirm the parent event while holding the same per-event advisory
-- lock used by event deletion. The API's earlier existence precheck is useful
-- for UX, but this check is the transactional authority for the mutation.
create or replace function public.clear_event_official_release_source_approved(
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

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 2));

  if not exists (
    select 1
    from public.market_events
    where event_id = input_event_id
  ) then
    raise exception 'event_not_found:%', input_event_id using errcode = 'P0002';
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

revoke all on function public.clear_event_official_release_source_approved(text, integer, text)
  from public, anon, authenticated;
grant execute on function public.clear_event_official_release_source_approved(text, integer, text)
  to service_role;

-- v4 means: v3 durability + latest-audit generation matching + an advisory-
-- locked live-parent check for approved clears.
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
      'public.set_event_official_release_source_approved(text, text, text, text, integer, text)'
    ) is not null
      and to_regprocedure(
        'public.get_audited_official_release_source_state(text)'
      ) is not null,
    to_regprocedure(
      'public.clear_event_official_release_source_approved(text, integer, text)'
    ) is not null
      and to_regprocedure(
        'public.tombstone_official_release_source_before_event_delete()'
      ) is not null,
    4;
$$;

revoke all on function public.verify_official_release_source_schema() from public, anon, authenticated;
grant execute on function public.verify_official_release_source_schema() to service_role;

commit;
