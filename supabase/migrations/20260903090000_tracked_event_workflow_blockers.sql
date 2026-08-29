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
