-- Extends the pre-restart Supabase schema gate to cover the calendar/
-- watchlist objects added by
-- supabase/migrations/20260824090000_calendar_watchlist_events.sql
-- (public.calendar_events, public.upsert_calendar_candidate(),
-- public.transition_calendar_event_status()).
--
-- Without this, GET/POST /api/v1/calendar/* would depend on Supabase
-- objects the deploy gate never checks: a missed out-of-band migration
-- could still pass verify_strategy_draft_schema() (which only ever knew
-- about the strategy-draft objects), the deploy would restart the backend
-- and report a healthy /health, and every calendar read/write would then
-- fail against missing tables/functions in production - exactly the
-- silent-gap failure mode
-- docs/event_configuration_storage.md's "Deploy gate: Supabase schema
-- verification" section calls out for any PR that adds a new Supabase
-- dependency.
--
-- verify_strategy_draft_schema()'s RETURNS TABLE shape grows again here
-- (4 columns -> 7), which - same as
-- 20260823090000_expectation_write_atomic_response_and_schema_version.sql
-- before it - `create or replace function` cannot do for an existing
-- function ("cannot change return type of existing function"). This is a
-- new migration, not an edit to that already-applied one, for the same
-- reason that file itself gives: editing an already-shipped migration in
-- place is invisible to anything that only checks "has this migration
-- file been applied," and there is no schema-version-marker concern here
-- to preserve either way - the calendar existence checks below are purely
-- additive, and strategy_draft_schema_version() (and the value it's
-- compared against) is untouched, so no bump is needed for it.
--
-- Single explicit transaction for the same reason as before: a `drop`
-- immediately followed by a `create` is only safe against a concurrent
-- caller because Postgres DDL is transactional, and a mid-file failure
-- must never leave the function dropped with nothing to replace it.
begin;

drop function if exists public.verify_strategy_draft_schema();

create function public.verify_strategy_draft_schema()
returns table (
  event_strategy_approvals_table_exists boolean,
  approve_strategy_draft_function_exists boolean,
  insert_next_expectation_version_function_exists boolean,
  schema_version_matches boolean,
  calendar_events_table_exists boolean,
  upsert_calendar_candidate_function_exists boolean,
  transition_calendar_event_status_function_exists boolean
)
language sql
security invoker
set search_path = public
as $$
  select
    to_regclass('public.event_strategy_approvals') is not null,
    to_regprocedure(
      'public.approve_strategy_draft(text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text)'
    ) is not null,
    to_regprocedure(
      'public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)'
    ) is not null,
    public.strategy_draft_schema_version() = 1,
    to_regclass('public.calendar_events') is not null,
    to_regprocedure(
      'public.upsert_calendar_candidate(text, text, text, text, text, date, text)'
    ) is not null,
    to_regprocedure(
      'public.transition_calendar_event_status(uuid, text, text)'
    ) is not null;
$$;

revoke all on function public.verify_strategy_draft_schema() from public;
grant execute on function public.verify_strategy_draft_schema() to service_role;

commit;
