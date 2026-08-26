-- Fail-closed deploy verifier for the manual official release source contract.
-- This stays separate from the older strategy/calendar verifier so adding this
-- dependency does not rewrite that RPC's established return shape.
begin;

create function public.verify_official_release_source_schema()
returns table (
  event_official_release_sources_table_exists boolean,
  set_event_official_release_source_function_exists boolean,
  clear_event_official_release_source_function_exists boolean
)
language sql
security invoker
set search_path = public
as $$
  select
    to_regclass('public.event_official_release_sources') is not null,
    to_regprocedure(
      'public.set_event_official_release_source(text, text, text, text, integer)'
    ) is not null,
    to_regprocedure(
      'public.clear_event_official_release_source(text, integer)'
    ) is not null;
$$;

revoke all on function public.verify_official_release_source_schema() from public, anon, authenticated;
grant execute on function public.verify_official_release_source_schema() to service_role;

commit;
