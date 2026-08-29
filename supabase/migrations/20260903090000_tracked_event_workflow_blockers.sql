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
