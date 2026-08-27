begin;

-- v5 strengthens the deploy contract by verifying the actual deletion trigger,
-- not only the trigger function. A dropped/disabled trigger would otherwise let
-- parent deletion leave an active source generation behind while the schema
-- verifier still claimed the deletion durability contract was present.
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
          -- FOR EACH ROW + BEFORE + DELETE.
          and trigger_row.tgtype = 11
          -- Enabled for ordinary/origin sessions or explicitly ALWAYS enabled.
          and trigger_row.tgenabled in ('O', 'A')
          and function_namespace.nspname = 'public'
          and trigger_function.proname = 'tombstone_official_release_source_before_event_delete'
      ),
    5;
$$;

revoke all on function public.verify_official_release_source_schema() from public, anon, authenticated;
grant execute on function public.verify_official_release_source_schema() to service_role;

commit;
