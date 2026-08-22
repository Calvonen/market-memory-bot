-- Version-gates the placeholder-preserving implementation of
-- upsert_calendar_candidate() installed by 20260826090000. The function's
-- signature did not change, so the existing to_regprocedure() check cannot
-- distinguish that implementation from the older, metadata-downgrading body.
--
-- Keep the marker and expanded verifier in one transaction: the marker must
-- never become visible unless the verifier that requires it is also installed.
begin;

create or replace function public.calendar_candidate_upsert_version()
returns integer
language sql
immutable
security invoker
set search_path = public
as $$
  select 1;
$$;

revoke all on function public.calendar_candidate_upsert_version() from public;
grant execute on function public.calendar_candidate_upsert_version() to service_role;

-- RETURNS TABLE grows from seven to eight columns, so PostgreSQL requires a
-- drop/recreate rather than CREATE OR REPLACE. Transactional DDL prevents a
-- concurrent verifier from observing a missing function.
drop function if exists public.verify_strategy_draft_schema();

create function public.verify_strategy_draft_schema()
returns table (
  event_strategy_approvals_table_exists boolean,
  approve_strategy_draft_function_exists boolean,
  insert_next_expectation_version_function_exists boolean,
  schema_version_matches boolean,
  calendar_events_table_exists boolean,
  upsert_calendar_candidate_function_exists boolean,
  transition_calendar_event_status_function_exists boolean,
  calendar_candidate_upsert_version_matches boolean
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
    ) is not null,
    public.calendar_candidate_upsert_version() = 1;
$$;

revoke all on function public.verify_strategy_draft_schema() from public;
grant execute on function public.verify_strategy_draft_schema() to service_role;

commit;
