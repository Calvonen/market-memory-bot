begin;

create table if not exists public.tracked_event_workflow_blockers (
    tracked_market_event_id uuid not null references public.tracked_market_events(id) on delete cascade,
    step_key text not null,
    provider text not null,
    blocker_code text not null,
    message text not null,
    resolved_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (tracked_market_event_id, step_key)
);

alter table public.tracked_event_workflow_blockers enable row level security;

grant select, insert, update, delete on public.tracked_event_workflow_blockers to service_role;

create index if not exists tracked_event_workflow_blockers_active_idx
    on public.tracked_event_workflow_blockers (tracked_market_event_id, step_key)
    where resolved_at is null;

comment on table public.tracked_event_workflow_blockers is
    'Durable workflow blockers keyed by tracked event so blockers can exist before a canonical release shell exists.';

create or replace function public.ensure_tracked_event_release_shell_with_blocker(
    input_tracked_event_id uuid
)
returns table (
    out_release_event_id text,
    out_blocker_code text
)
language plpgsql
security definer
set search_path = public
as $$
declare
    release_id text;
    error_text text;
begin
    begin
        select shell.out_release_event_id
          into release_id
          from public.ensure_tracked_event_release_shell(input_tracked_event_id) as shell;
    exception when others then
        error_text := lower(sqlerrm);
        if error_text like '%tracked_release_calendar_binding_identity_conflict%'
           or error_text like '%tracked_release_shell_identity_conflict%' then
            insert into public.tracked_event_workflow_blockers (
                tracked_market_event_id,
                step_key,
                provider,
                blocker_code,
                message,
                resolved_at,
                updated_at
            ) values (
                input_tracked_event_id,
                'release',
                'canonical_release_worker',
                case
                    when error_text like '%tracked_release_calendar_binding_identity_conflict%'
                        then 'tracked_release_calendar_binding_identity_conflict'
                    else 'tracked_release_shell_identity_conflict'
                end,
                left(sqlerrm, 500),
                null,
                now()
            )
            on conflict (tracked_market_event_id, step_key) do update
            set provider = excluded.provider,
                blocker_code = excluded.blocker_code,
                message = excluded.message,
                resolved_at = null,
                updated_at = now();

            return query select null::text,
                case
                    when error_text like '%tracked_release_calendar_binding_identity_conflict%'
                        then 'tracked_release_calendar_binding_identity_conflict'
                    else 'tracked_release_shell_identity_conflict'
                end;
            return;
        end if;
        raise;
    end;

    update public.tracked_event_workflow_blockers
       set resolved_at = now(),
           updated_at = now()
     where tracked_market_event_id = input_tracked_event_id
       and step_key = 'release'
       and resolved_at is null;

    return query select release_id, null::text;
end;
$$;

revoke all on function public.ensure_tracked_event_release_shell_with_blocker(uuid) from public;
grant execute on function public.ensure_tracked_event_release_shell_with_blocker(uuid) to service_role;

create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 13;
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
  runtime_schema_version integer
)
language sql
stable
security invoker
as $$
  select
    to_regclass('public.tracked_market_events') is not null,
    to_regclass('public.tracked_market_event_reactions') is not null,
    exists (
      select 1
      from information_schema.columns
      where table_schema = 'public'
        and table_name = 'tracked_market_events'
        and column_name = 'event_date'
        and data_type = 'date'
    ),
    to_regprocedure(
      'public.upsert_tracked_market_event(text,text,text,text,text,text,text,timestamptz,text,text,uuid)'
    ) is not null,
    to_regprocedure(
      'public.arm_tracked_market_event_resolution(uuid,bigint,text,text,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_reference(uuid,numeric,timestamptz,text,bigint,text,text,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_reaction_anchor(uuid,timestamptz,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_config_snapshot(uuid,jsonb,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_pre_event_context(uuid,jsonb,text,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_pre_event_context_if_current(uuid,jsonb,text,text,timestamptz)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_pre_event_context_validated(uuid,jsonb,text,text,timestamptz,timestamptz)'
    ) is not null,
    to_regprocedure(
      'public.validate_tracked_market_event_pre_event_context_if_current(uuid,timestamptz)'
    ) is not null,
    to_regprocedure(
      'public.fail_tracked_market_event_pre_event_deadline_if_current(uuid,timestamptz,text,text)'
    ) is not null,
    to_regprocedure(
      'public.fail_tracked_market_event_stale_context_if_current(uuid,timestamptz,text,text)'
    ) is not null,
    to_regprocedure(
      'public.promote_calendar_event_to_tracked_runtime(uuid,text,text,text,text,date,timestamptz,text,text)'
    ) is not null,
    public.calendar_runtime_untrack_guard_version() = 1,
    to_regprocedure('public.ensure_calendar_release_shell(uuid)') is not null,
    public.calendar_release_shell_version() = 1,
    to_regprocedure('public.ensure_tracked_event_release_shell(uuid)') is not null,
    to_regclass('public.tracked_event_workflow_blockers') is not null,
    to_regprocedure('public.ensure_tracked_event_release_shell_with_blocker(uuid)') is not null,
    exists (
      select 1
      from pg_trigger t
      join pg_class c on c.oid = t.tgrelid
      join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public'
        and c.relname = 'tracked_market_events'
        and t.tgname = 'tracked_market_events_calendarless_release_shell_after_date_write'
        and not t.tgisinternal
    ),
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;

commit;
