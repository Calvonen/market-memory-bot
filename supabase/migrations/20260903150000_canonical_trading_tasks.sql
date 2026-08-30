-- Canonical execution intent. This is deliberately separate from tracked
-- instruments/events: observation never creates execution authority.

create table if not exists public.trading_tasks (
  id uuid primary key default gen_random_uuid(),
  tracked_event_id uuid not null references public.tracked_market_events(id) on delete restrict,
  source_event_id text not null,
  instrument text not null,
  mode text not null check (mode in ('PAPER', 'LIVE')),
  state text not null default 'pending' check (state in ('pending', 'approved', 'cancelled')),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default now(),
  approved_by text null check (approved_by is null or length(btrim(approved_by)) between 1 and 200),
  approved_at timestamptz null,
  cancelled_by text null check (cancelled_by is null or length(btrim(cancelled_by)) between 1 and 200),
  cancelled_at timestamptz null,
  check (
    (state = 'pending' and approved_by is null and approved_at is null and cancelled_by is null and cancelled_at is null)
    or (state = 'approved' and approved_by is not null and approved_at is not null and cancelled_by is null and cancelled_at is null)
    or (state = 'cancelled' and cancelled_by is not null and cancelled_at is not null)
  )
);

-- At most one live request per event/mode, but cancellation releases the slot
-- so a later explicit request creates a new task instead of mutating history.
create unique index if not exists trading_tasks_active_event_mode_uidx
  on public.trading_tasks(tracked_event_id, mode)
  where state in ('pending', 'approved');

create index if not exists trading_tasks_source_event_state_idx
  on public.trading_tasks(source_event_id, state);

alter table public.trading_tasks enable row level security;
-- Fail closed even on databases where service_role inherited DML through
-- default privileges or an earlier partial install. RPCs below are the only
-- write boundary; service_role gets SELECT back explicitly afterwards.
revoke all on table public.trading_tasks from public, anon, authenticated, service_role;
grant select on table public.trading_tasks to service_role;

create or replace function public.create_trading_task(
  input_tracked_event_id uuid,
  input_source_event_id text,
  input_instrument text,
  input_mode text,
  input_actor text
)
returns public.trading_tasks
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  event_row public.tracked_market_events%rowtype;
  canonical_source_event_id text;
  actor text := btrim(input_actor);
  instrument_value text := upper(btrim(input_instrument));
  mode_value text := upper(btrim(input_mode));
  created public.trading_tasks%rowtype;
begin
  if input_tracked_event_id is null then
    raise exception 'trading_task_invalid_tracked_event_id';
  end if;
  if input_source_event_id is null or btrim(input_source_event_id) = '' then
    raise exception 'trading_task_invalid_source_event_id';
  end if;
  if instrument_value = '' then
    raise exception 'trading_task_invalid_instrument';
  end if;
  if mode_value not in ('PAPER', 'LIVE') then
    raise exception 'trading_task_invalid_mode';
  end if;
  if actor is null or length(actor) not between 1 and 200 then
    raise exception 'trading_task_invalid_actor';
  end if;

  select * into event_row
  from public.tracked_market_events
  where id = input_tracked_event_id;
  if not found then
    raise exception 'trading_task_tracked_event_not_found';
  end if;

  canonical_source_event_id := case
    when event_row.calendar_event_id is not null then 'calendar:' || event_row.calendar_event_id::text
    else 'tracked:' || event_row.id::text
  end;

  if input_source_event_id <> canonical_source_event_id then
    raise exception 'trading_task_event_identity_mismatch';
  end if;
  if upper(btrim(event_row.instrument)) <> instrument_value then
    raise exception 'trading_task_instrument_mismatch';
  end if;

  begin
    insert into public.trading_tasks (
      tracked_event_id, source_event_id, instrument, mode, state, created_by
    ) values (
      input_tracked_event_id, canonical_source_event_id, instrument_value, mode_value, 'pending', actor
    )
    returning * into created;
    return created;
  exception
    when unique_violation then
      -- Retry-safe creation: if the original insert committed but its RPC
      -- response was lost, return the exact still-active canonical task so the
      -- caller recovers its server-generated id and current lifecycle state.
      select * into created
      from public.trading_tasks
      where tracked_event_id = input_tracked_event_id
        and mode = mode_value
        and state in ('pending', 'approved')
      limit 2;

      if not found then
        raise exception 'trading_task_creation_conflict';
      end if;
      if created.source_event_id <> canonical_source_event_id
         or upper(btrim(created.instrument)) <> instrument_value then
        raise exception 'trading_task_creation_conflict';
      end if;
      return created;
  end;
end;
$$;

create or replace function public.approve_trading_task(
  input_task_id uuid,
  input_actor text
)
returns public.trading_tasks
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  actor text := btrim(input_actor);
  approved public.trading_tasks%rowtype;
begin
  if input_task_id is null then
    raise exception 'trading_task_invalid_id';
  end if;
  if actor is null or length(actor) not between 1 and 200 then
    raise exception 'trading_task_invalid_actor';
  end if;

  update public.trading_tasks
  set state = 'approved', approved_by = actor, approved_at = now()
  where id = input_task_id and state = 'pending'
  returning * into approved;

  if found then
    return approved;
  end if;
  if not exists (select 1 from public.trading_tasks where id = input_task_id) then
    raise exception 'trading_task_not_found';
  end if;
  raise exception 'trading_task_not_pending';
end;
$$;

create or replace function public.cancel_trading_task(
  input_task_id uuid,
  input_actor text
)
returns public.trading_tasks
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  actor text := btrim(input_actor);
  cancelled public.trading_tasks%rowtype;
begin
  if input_task_id is null then
    raise exception 'trading_task_invalid_id';
  end if;
  if actor is null or length(actor) not between 1 and 200 then
    raise exception 'trading_task_invalid_actor';
  end if;

  update public.trading_tasks
  set state = 'cancelled', cancelled_by = actor, cancelled_at = now()
  where id = input_task_id and state in ('pending', 'approved')
  returning * into cancelled;

  if found then
    return cancelled;
  end if;
  if not exists (select 1 from public.trading_tasks where id = input_task_id) then
    raise exception 'trading_task_not_found';
  end if;
  raise exception 'trading_task_already_cancelled';
end;
$$;

revoke all on function public.create_trading_task(uuid, text, text, text, text) from public, anon, authenticated;
revoke all on function public.approve_trading_task(uuid, text) from public, anon, authenticated;
revoke all on function public.cancel_trading_task(uuid, text) from public, anon, authenticated;
grant execute on function public.create_trading_task(uuid, text, text, text, text) to service_role;
grant execute on function public.approve_trading_task(uuid, text) to service_role;
grant execute on function public.cancel_trading_task(uuid, text) to service_role;
