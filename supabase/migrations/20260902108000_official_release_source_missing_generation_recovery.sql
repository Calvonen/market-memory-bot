begin;

-- A market_events DELETE could have won the lock race between migrations 1040
-- and 1050 while the old ON DELETE CASCADE source FK was still attached. The
-- audit trail survives that delete, so reconstruct any missing durable source
-- generation from the latest audit entry before declaring the schema ready.
lock table public.market_events in share mode;
lock table public.event_official_release_sources in access exclusive mode;

create temporary table official_release_source_missing_generation
on commit drop
as
select distinct on (audit.event_id)
  audit.event_id,
  audit.action,
  audit.version
from public.event_official_release_source_audit audit
where not exists (
  select 1
  from public.event_official_release_sources source
  where source.event_id = audit.event_id
)
order by audit.event_id, audit.id desc;

-- If the retained latest audit is already a clear, recreate that exact
-- tombstone/version. No new audit row is needed because the clear already
-- explains the durable inactive state.
insert into public.event_official_release_sources (
  event_id,
  source_kind,
  source_url,
  source_title,
  is_active,
  version,
  created_at,
  updated_at
)
select
  missing.event_id,
  null,
  null,
  null,
  false,
  missing.version,
  clock_timestamp(),
  clock_timestamp()
from official_release_source_missing_generation missing
where missing.action = 'clear';

-- If the latest retained audit is a set, the missing source row proves the old
-- cascade removed an active generation before the deletion tombstone trigger
-- existed. Materialize the logically missing clear as version + 1 and record
-- that recovery explicitly so the audit history and CAS generation stay
-- monotonic across event deletion/recreation.
with recovered as (
  insert into public.event_official_release_sources (
    event_id,
    source_kind,
    source_url,
    source_title,
    is_active,
    version,
    created_at,
    updated_at
  )
  select
    missing.event_id,
    null,
    null,
    null,
    false,
    missing.version + 1,
    clock_timestamp(),
    clock_timestamp()
  from official_release_source_missing_generation missing
  where missing.action = 'set'
  returning event_id, version
)
insert into public.event_official_release_source_audit (
  event_id,
  action,
  actor,
  version,
  source_kind,
  source_url,
  source_title
)
select
  recovered.event_id,
  'clear',
  'migration:pre-durable-delete-recovery',
  recovered.version,
  null,
  null,
  null
from recovered;

-- v6 means the v5 trigger contract plus recovery of any durable generation
-- lost in the historical 1040 -> 1050 cascade window.
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
    6;
$$;

revoke all on function public.verify_official_release_source_schema() from public, anon, authenticated;
grant execute on function public.verify_official_release_source_schema() to service_role;

commit;
