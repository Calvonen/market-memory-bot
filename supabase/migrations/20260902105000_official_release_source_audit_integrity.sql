begin;

-- Revoke the legacy unaudited writers again in a committed transaction. This
-- migration may be applied after 1040 on a rolling deployment where old
-- processes are still alive; no new service-role invocation may enter them
-- after this commit becomes visible.
revoke all on function public.set_event_official_release_source(text, text, text, text, integer)
  from service_role;
revoke all on function public.clear_event_official_release_source(text, integer)
  from service_role;

commit;

begin;

-- A legacy call that passed EXECUTE before the revoke can be queued on the
-- per-event advisory lock before touching event_official_release_sources. Drain
-- those queues explicitly for every currently meaningful event/source identity.
do $$
declare
  item record;
begin
  for item in
    select event_id
    from (
      select event_id from public.market_events
      union
      select event_id from public.event_official_release_sources
    ) identities
    order by event_id
  loop
    perform pg_advisory_xact_lock(hashtextextended(item.event_id, 2));
  end loop;
end;
$$;

lock table public.event_official_release_sources in access exclusive mode;

-- If a pre-revoke legacy writer escaped the 1040 invalidation window, preserve
-- what it wrote as an explicitly unaudited migration observation, then convert
-- it to a version-advancing tombstone. Runtime reads below also reject any
-- future active row that lacks a matching audited set record, so such a row can
-- never drive ingestion even if an unexpected legacy process survives.
create temporary table official_release_source_unaudited_repair
on commit drop
as
select
  source.event_id,
  source.version,
  source.source_kind,
  source.source_url,
  source.source_title
from public.event_official_release_sources source
where source.is_active
  and not exists (
    select 1
    from public.event_official_release_source_audit audit
    where audit.event_id = source.event_id
      and audit.action = 'set'
      and audit.version = source.version
      and audit.source_kind is not distinct from source.source_kind
      and audit.source_url is not distinct from source.source_url
      and audit.source_title is not distinct from source.source_title
  );

insert into public.event_official_release_source_audit (
  event_id, action, actor, version, source_kind, source_url, source_title
)
select
  event_id,
  'set',
  'migration:post-revoke-unaudited-source',
  version,
  source_kind,
  source_url,
  source_title
from official_release_source_unaudited_repair;

with invalidated as (
  update public.event_official_release_sources source
  set
    source_kind = null,
    source_url = null,
    source_title = null,
    is_active = false,
    version = source.version + 1,
    updated_at = clock_timestamp()
  from official_release_source_unaudited_repair repair
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
  'migration:post-revoke-unaudited-invalidation',
  version,
  null,
  null,
  null
from invalidated;

-- Source version state must survive market-event cleanup/recreation. The write
-- RPC still checks market_events existence before accepting a new approval, but
-- the source row itself now persists as the generation/tombstone record.
alter table public.event_official_release_sources
  drop constraint if exists event_official_release_sources_event_id_fkey;

comment on column public.event_official_release_sources.event_id is
  'Canonical event identity retained independently of market_events so source CAS versions never reset after event deletion/recreation.';

create or replace function public.tombstone_official_release_source_before_event_delete()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  new_version integer;
begin
  perform pg_advisory_xact_lock(hashtextextended(old.event_id, 2));

  update public.event_official_release_sources
  set
    source_kind = null,
    source_url = null,
    source_title = null,
    is_active = false,
    version = version + 1,
    updated_at = clock_timestamp()
  where event_id = old.event_id
    and is_active
  returning version into new_version;

  if found then
    insert into public.event_official_release_source_audit (
      event_id, action, actor, version, source_kind, source_url, source_title
    ) values (
      old.event_id,
      'clear',
      'system:market-event-delete',
      new_version,
      null,
      null,
      null
    );
  end if;

  return old;
end;
$$;

revoke all on function public.tombstone_official_release_source_before_event_delete()
  from public, anon, authenticated, service_role;

drop trigger if exists tombstone_official_release_source_before_event_delete
  on public.market_events;
create trigger tombstone_official_release_source_before_event_delete
before delete on public.market_events
for each row
execute function public.tombstone_official_release_source_before_event_delete();

-- Canonical runtime read: an active source is exposed only when its exact
-- current version/content has a matching audited `set` record. Missing,
-- tombstoned, or unaudited active rows remain inactive to callers while still
-- exposing the persisted CAS version.
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
    select exists (
      select 1
      from public.event_official_release_source_audit audit
      where audit.event_id = source_row.event_id
        and audit.action = 'set'
        and audit.version = source_row.version
        and audit.source_kind is not distinct from source_row.source_kind
        and audit.source_url is not distinct from source_row.source_url
        and audit.source_title is not distinct from source_row.source_title
    ) into audited_active;
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

-- v3 means: audited write RPCs + legacy invalidation + durable source
-- generations + audit-backed runtime reads.
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
    3;
$$;

revoke all on function public.verify_official_release_source_schema() from public, anon, authenticated;
grant execute on function public.verify_official_release_source_schema() to service_role;

commit;
