-- Append-only operator attribution for an explicit decision not to ingest a release.
create table public.tracked_event_release_skip_audit (
  id bigint generated always as identity primary key,
  tracked_event_id uuid not null,
  release_event_id text not null,
  actor text not null check (actor = btrim(actor) and length(actor) between 1 and 200),
  reason text not null check (reason = btrim(reason) and length(reason) between 1 and 1000),
  created_at timestamptz not null default now()
);

comment on table public.tracked_event_release_skip_audit is
  'Append-only attribution for authenticated tracked-event release skip decisions.';

alter table public.tracked_event_release_skip_audit enable row level security;
revoke all on table public.tracked_event_release_skip_audit
  from public, anon, authenticated, service_role;
grant select on table public.tracked_event_release_skip_audit to service_role;

create or replace function public.record_tracked_event_release_skip(
  input_tracked_event_id uuid,
  input_release_event_id text,
  input_actor text,
  input_reason text
) returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  audit_id bigint;
  expected_release_event_id text;
begin
  if input_actor is null or input_actor <> btrim(input_actor)
     or length(input_actor) < 1 or length(input_actor) > 200 then
    raise exception 'invalid release skip actor';
  end if;
  if input_release_event_id is null or btrim(input_release_event_id) = '' then
    raise exception 'invalid release skip identity';
  end if;
  if input_reason is null or input_reason <> btrim(input_reason)
     or length(input_reason) < 1 or length(input_reason) > 1000 then
    raise exception 'invalid release skip reason';
  end if;

  select case
    when calendar_event_id is not null then 'calendar:' || calendar_event_id::text
    else 'tracked:' || id::text
  end into expected_release_event_id
  from public.tracked_market_events
  where id = input_tracked_event_id;

  if expected_release_event_id is null then
    raise exception 'tracked event not found';
  end if;
  if input_release_event_id <> expected_release_event_id
     or not exists (
       select 1 from public.market_events where event_id = expected_release_event_id
     ) then
    raise exception 'tracked release shell identity conflict';
  end if;

  insert into public.tracked_event_release_skip_audit (
    tracked_event_id, release_event_id, actor, reason
  ) values (
    input_tracked_event_id, input_release_event_id, input_actor, input_reason
  ) returning id into audit_id;
  return audit_id;
end;
$$;

revoke all on function public.record_tracked_event_release_skip(uuid, text, text, text)
  from public, anon, authenticated;
grant execute on function public.record_tracked_event_release_skip(uuid, text, text, text)
  to service_role;

-- The release-skip audit is a production dependency of the explicit skip endpoint. Extend
-- the existing deploy gate so the application cannot start against a database
-- where this out-of-band migration has not yet been applied.
create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 15;
$$;

revoke all on function public.tracked_event_runtime_schema_version from public;
grant execute on function public.tracked_event_runtime_schema_version to service_role;

drop function if exists public.verify_tracked_event_runtime_schema();

create function public.verify_tracked_event_runtime_schema()
returns table (
  tracked_market_events_table_exists boolean,
  tracked_market_event_reactions_table_exists boolean,
  tracked_market_event_event_date_column_exists boolean,
  upsert_tracked_market_event_function_exists boolean,
  arm_tracked_market_event_resolution_function_exists boolean,
  capture_tracked_market_event_reference_function_exists boolean,
  capture_tracked_market_event_reaction_anchor_function_exists boolean,
  capture_tracked_market_event_config_snapshot_function_exists boolean,
  capture_tracked_market_event_pre_event_context_function_exists boolean,
  capture_tracked_market_event_pre_event_context_if_current_function_exists boolean,
  capture_tracked_market_event_pre_event_context_validated_function_exists boolean,
  validate_tracked_market_event_pre_event_context_if_current_function_exists boolean,
  fail_tracked_market_event_pre_event_deadline_if_current_function_exists boolean,
  fail_tracked_market_event_stale_context_if_current_function_exists boolean,
  promote_calendar_event_to_tracked_runtime_function_exists boolean,
  calendar_runtime_untrack_guard_version_matches boolean,
  ensure_calendar_release_shell_function_exists boolean,
  calendar_release_shell_version_matches boolean,
  ensure_tracked_event_release_shell_function_exists boolean,
  tracked_event_workflow_blockers_table_exists boolean,
  ensure_tracked_event_release_shell_with_blocker_function_exists boolean,
  calendarless_release_shell_trigger_exists boolean,
  tracked_event_release_ingestion_audit_table_exists boolean,
  record_tracked_event_release_ingestion_attempt_function_exists boolean,
  tracked_event_release_skip_audit_table_exists boolean,
  record_tracked_event_release_skip_function_exists boolean,
  runtime_schema_version integer
)
language sql
stable
security invoker
as $$
  select
    to_regclass('public.tracked_market_events') is not null,
    to_regclass('public.tracked_market_event_reactions') is not null,
    exists (select 1 from information_schema.columns where table_schema = 'public' and table_name = 'tracked_market_events' and column_name = 'event_date' and data_type = 'date'),
    to_regprocedure('public.upsert_tracked_market_event(text,text,text,text,text,text,text,timestamptz,text,text,uuid)') is not null,
    to_regprocedure('public.arm_tracked_market_event_resolution(uuid,bigint,text,text,text)') is not null,
    to_regprocedure('public.capture_tracked_market_event_reference(uuid,numeric,timestamptz,text,bigint,text,text,text)') is not null,
    to_regprocedure('public.capture_tracked_market_event_reaction_anchor(uuid,timestamptz,text)') is not null,
    to_regprocedure('public.capture_tracked_market_event_config_snapshot(uuid,jsonb,text)') is not null,
    to_regprocedure('public.capture_tracked_market_event_pre_event_context(uuid,jsonb,text,text)') is not null,
    to_regprocedure('public.capture_tracked_market_event_pre_event_context_if_current(uuid,jsonb,text,text,timestamptz)') is not null,
    to_regprocedure('public.capture_tracked_market_event_pre_event_context_validated(uuid,jsonb,text,text,timestamptz,timestamptz)') is not null,
    to_regprocedure('public.validate_tracked_market_event_pre_event_context_if_current(uuid,timestamptz)') is not null,
    to_regprocedure('public.fail_tracked_market_event_pre_event_deadline_if_current(uuid,timestamptz,text,text)') is not null,
    to_regprocedure('public.fail_tracked_market_event_stale_context_if_current(uuid,timestamptz,text,text)') is not null,
    to_regprocedure('public.promote_calendar_event_to_tracked_runtime(uuid,text,text,text,text,date,timestamptz,text,text)') is not null,
    public.calendar_runtime_untrack_guard_version() = 1,
    to_regprocedure('public.ensure_calendar_release_shell(uuid)') is not null,
    public.calendar_release_shell_version() = 1,
    to_regprocedure('public.ensure_tracked_event_release_shell(uuid)') is not null,
    to_regclass('public.tracked_event_workflow_blockers') is not null,
    to_regprocedure('public.ensure_tracked_event_release_shell_with_blocker(uuid)') is not null,
    exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relname = 'tracked_market_events' and t.tgname = 'tracked_market_events_calendarless_release_shell_after_date_write' and not t.tgisinternal),
    to_regclass('public.tracked_event_release_ingestion_audit') is not null,
    to_regprocedure('public.record_tracked_event_release_ingestion_attempt(uuid,text,text,text)') is not null,
    to_regclass('public.tracked_event_release_skip_audit') is not null,
    to_regprocedure('public.record_tracked_event_release_skip(uuid,text,text,text)') is not null,
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;
