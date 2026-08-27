begin;

-- A market_events DELETE could have won the lock race between migrations 1040
-- and 1050 while the old ON DELETE CASCADE source FK was still attached. The
-- audit trail survives that delete, so reconstruct any missing durable source
-- generation from the latest audit entry before declaring the schema ready.
lock table public.market_events in share mode;
lock table public.event_official_release_sources in access exclusive mode;

create temporary table official_release_source_generation_recovery
on commit drop
as
with latest_audit as (
  select distinct on (audit.event_id)
    audit.event_id,
    audit.action,
    audit.version
  from public.event_official_release_source_audit audit
  order by audit.event_id, audit.id desc
)
select
  latest.event_id,
  latest.action,
  latest.version,
  source.version as source_version
from latest_audit latest
left join public.event_official_release_sources source
  on source.event_id = latest.event_id
where source.event_id is null
   or source.version < latest.version;

-- If the retained latest audit is already a clear, restore or advance the
-- durable tombstone to that exact audited generation. This also repairs stale
-- low-version tombstones recreated by pre-revoke legacy calls.
insert into public.event_official_release_sources (
  event_id, source_kind, source_url, source_title, is_active, version, created_at, updated_at
)
select
  recovery.event_id, null, null, null, false, recovery.version,
  clock_timestamp(), clock_timestamp()
from official_release_source_generation_recovery recovery
where recovery.action = 'clear'
on conflict (event_id) do update
set
  source_kind = null,
  source_url = null,
  source_title = null,
  is_active = false,
  version = excluded.version,
  updated_at = clock_timestamp()
where public.event_official_release_sources.version < excluded.version;

-- If the latest retained audit is a set, the durable row is absent or trails
-- that generation. Materialize the logically missing deletion clear at the
-- next version and record it explicitly so future CAS writes remain monotonic.
with recovered as (
  insert into public.event_official_release_sources (
    event_id, source_kind, source_url, source_title, is_active, version, created_at, updated_at
  )
  select
    recovery.event_id, null, null, null, false, recovery.version + 1,
    clock_timestamp(), clock_timestamp()
  from official_release_source_generation_recovery recovery
  where recovery.action = 'set'
  on conflict (event_id) do update
  set
    source_kind = null,
    source_url = null,
    source_title = null,
    is_active = false,
    version = excluded.version,
    updated_at = clock_timestamp()
  where public.event_official_release_sources.version < excluded.version
  returning event_id, version
)
insert into public.event_official_release_source_audit (
  event_id, action, actor, version, source_kind, source_url, source_title
)
select
  recovered.event_id,
  'clear',
  'migration:pre-durable-delete-recovery',
  recovered.version,
  null, null, null
from recovered
where not exists (
  select 1
  from public.event_official_release_source_audit audit
  where audit.event_id = recovered.event_id
    and audit.action = 'clear'
    and audit.version = recovered.version
);

-- v7 means the v5 trigger contract plus recovery of missing or stale durable
-- generations lost/recreated in the historical 1040 -> 1050 cascade window.
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
      and exists (
        select 1
        from pg_catalog.pg_trigger trigger_row
        join pg_catalog.pg_class relation
          on relation.oid = trigger_row.tgrelid
        join pg_catalog.pg_namespace relation_namespace
          on relation_namespace.oid = relation.relnamespace
        join pg_catalog.pg_proc trigger_function
          on trigger_function.oid = trigger_row.tgfoid
        join pg_catalog.pg_namespace function_namespace
          on function_namespace.oid = trigger_function.pronamespace
        where relation_namespace.nspname = 'public'
          and relation.relname = 'market_events'
          and trigger_row.tgname = 'tombstone_official_release_source_before_event_delete'
          and not trigger_row.tgisinternal
          and trigger_row.tgtype = 11
          and trigger_row.tgenabled in ('O', 'A')
          and function_namespace.nspname = 'public'
          and trigger_function.proname = 'tombstone_official_release_source_before_event_delete'
      ),
    7;
$$;

revoke all on function public.verify_official_release_source_schema() from public, anon, authenticated;
grant execute on function public.verify_official_release_source_schema() to service_role;

commit;
