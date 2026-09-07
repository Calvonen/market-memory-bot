-- Carry forward assistant materialization safety after the v5 receipt hardening.
-- This migration is intentionally idempotent with the preceding hardening migrations.

begin;

create or replace function public.guard_assistant_proposal_materialized_lineage()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  binding_exists boolean := false;
  receipt_guard_ready boolean := false;
begin
  if new.status is distinct from 'materialized'
     or old.status is not distinct from 'materialized' then
    return new;
  end if;

  if to_regclass('public.assistant_proposal_materializations') is null then
    raise exception 'assistant_materialization_lineage_not_installed' using errcode = '55000';
  end if;

  select exists (
    select 1 from public.assistant_proposal_materializations m
    where m.proposal_id = new.id and m.review_round = new.review_round
  ) into binding_exists;

  if not binding_exists then
    raise exception 'assistant_materialization_requires_durable_lineage' using errcode = '55000';
  end if;

  if to_regclass('public.event_strategy_approvals') is not null
     and to_regprocedure('public.guard_assistant_proposal_receipt_snapshot()') is not null then
    select exists (
      select 1 from pg_trigger
      where tgrelid = 'public.event_strategy_approvals'::regclass
        and tgname = 'assistant_proposal_receipt_snapshot_guard'
        and not tgisinternal
        and tgenabled in ('O', 'A')
        and tgfoid = to_regprocedure('public.guard_assistant_proposal_receipt_snapshot()')
        and tgtype = 7
    ) into receipt_guard_ready;
  end if;

  if not receipt_guard_ready then
    raise exception 'assistant_materialization_requires_receipt_snapshot_guard' using errcode = '55000';
  end if;

  return new;
end;
$$;

revoke all on function public.guard_assistant_proposal_materialized_lineage()
  from public, anon, authenticated;

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
  calendar_candidate_upsert_implementation_version integer,
  assistant_materialization_lineage_table_exists boolean,
  assistant_materialization_lineage_acl_locked boolean,
  assistant_lineage_enforcement_exists boolean
)
language sql
security invoker
set search_path = public
as $$
  select
    to_regclass('public.event_strategy_approvals') is not null
      and not has_table_privilege('service_role', 'public.event_strategy_approvals', 'INSERT'),
    to_regprocedure('public.approve_strategy_draft(text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text)') is not null
      and to_regprocedure('public.approve_assistant_proposal_strategy_draft(uuid, integer, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text, text, text, text, integer, boolean, text)') is not null,
    to_regprocedure('public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)') is not null,
    public.strategy_draft_schema_version() = 5,
    to_regclass('public.calendar_events') is not null,
    to_regprocedure('public.upsert_calendar_candidate(text, text, text, text, text, date, text)') is not null,
    to_regprocedure('public.transition_calendar_event_status(uuid, text, text)') is not null,
    public.calendar_candidate_upsert_version() = 3,
    public.calendar_candidate_upsert_version(),
    to_regclass('public.assistant_proposal_materializations') is not null,
    not has_table_privilege('service_role', 'public.assistant_proposal_materializations', 'INSERT')
      and not has_table_privilege('service_role', 'public.assistant_proposal_materializations', 'UPDATE')
      and not has_table_privilege('service_role', 'public.assistant_proposal_materializations', 'DELETE'),
    to_regprocedure('public.assistant_normalize_symbol(text)') is not null
      and to_regprocedure('public.guard_assistant_proposal_receipt_snapshot()') is not null
      and to_regprocedure('public.guard_assistant_proposal_materialized_lineage()') is not null
      and exists (
        select 1 from pg_trigger
        where tgrelid = 'public.event_strategy_approvals'::regclass
          and tgname = 'assistant_proposal_receipt_snapshot_guard'
          and not tgisinternal and tgenabled in ('O', 'A')
          and tgfoid = to_regprocedure('public.guard_assistant_proposal_receipt_snapshot()')
          and tgtype = 7
      )
      and exists (
        select 1 from pg_trigger
        where tgrelid = 'public.assistant_event_proposals'::regclass
          and tgname = 'assistant_proposal_materialized_lineage_guard'
          and not tgisinternal and tgenabled in ('O', 'A')
          and tgfoid = to_regprocedure('public.guard_assistant_proposal_materialized_lineage()')
          and tgtype = 19
      )
      and to_regprocedure('public.upsert_assistant_proposal_canonical_tracked_event(uuid, integer, integer, text, text, text, text, text, text, text, timestamptz, date, text, text, text)') is not null;
$$;

revoke all on function public.verify_strategy_draft_schema()
  from public, anon, authenticated;
grant execute on function public.verify_strategy_draft_schema() to service_role;

commit;
