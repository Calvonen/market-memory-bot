-- Restore the combined strategy + calendar deploy-gate contract after the
-- assistant migrations finish advancing strategy_draft_schema_version() to v4.
-- Keep the assistant receipt-table ACL check introduced by the v4 migration.

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
  transition_calendar_event_status_function_exists boolean,
  calendar_candidate_upsert_version_matches boolean,
  calendar_candidate_upsert_implementation_version integer
)
language sql
security invoker
set search_path = public
as $$
  select
    to_regclass('public.event_strategy_approvals') is not null
      and not has_table_privilege(
        'service_role', 'public.event_strategy_approvals', 'INSERT'
      ),
    to_regprocedure(
      'public.approve_strategy_draft(text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text)'
    ) is not null
      and to_regprocedure(
        'public.approve_assistant_proposal_strategy_draft(uuid, integer, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text, text, text, text, integer, boolean, text)'
      ) is not null,
    to_regprocedure(
      'public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)'
    ) is not null,
    public.strategy_draft_schema_version() = 4,
    to_regclass('public.calendar_events') is not null,
    to_regprocedure(
      'public.upsert_calendar_candidate(text, text, text, text, text, date, text)'
    ) is not null,
    to_regprocedure(
      'public.transition_calendar_event_status(uuid, text, text)'
    ) is not null,
    public.calendar_candidate_upsert_version() = 3,
    public.calendar_candidate_upsert_version();
$$;

revoke all on function public.verify_strategy_draft_schema()
  from public, anon, authenticated;
grant execute on function public.verify_strategy_draft_schema()
  to service_role;

commit;
