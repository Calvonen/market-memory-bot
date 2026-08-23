-- Overnight events may occur while the exchange is closed. In that case the
-- 1m/5m/15m monitoring stages must start from the first real post-event
-- tradable candle, not burn their first 30 minutes while no market exists.

alter table public.tracked_market_events
  add column if not exists reaction_anchor_at timestamptz null;

alter table public.tracked_market_events
  drop constraint if exists tracked_market_events_reaction_anchor_after_event;
alter table public.tracked_market_events
  add constraint tracked_market_events_reaction_anchor_after_event
  check (reaction_anchor_at is null or reaction_anchor_at >= event_at);

create or replace function public.capture_tracked_market_event_reaction_anchor(
  input_event_id uuid,
  input_reaction_anchor_at timestamptz,
  input_actor text
)
returns public.tracked_market_events
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
begin
  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;
  if existing_row.status not in ('tracked', 'monitoring') then
    raise exception 'tracked_market_event_not_monitorable';
  end if;
  if existing_row.reference_price is null then
    raise exception 'tracked_market_event_reference_missing';
  end if;
  if input_reaction_anchor_at < existing_row.event_at then
    raise exception 'reaction_anchor_before_event';
  end if;

  if existing_row.reaction_anchor_at is not null then
    if existing_row.reaction_anchor_at = input_reaction_anchor_at then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_reaction_anchor_locked';
  end if;

  update public.tracked_market_events
  set reaction_anchor_at = input_reaction_anchor_at,
      updated_by = input_actor,
      updated_at = now()
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.capture_tracked_market_event_reaction_anchor from public;
grant execute on function public.capture_tracked_market_event_reaction_anchor to service_role;

-- PostgreSQL cannot change the OUT-column shape of an existing function with
-- CREATE OR REPLACE, so replace this read-only schema marker explicitly.
drop function if exists public.verify_tracked_event_runtime_schema();

create function public.verify_tracked_event_runtime_schema()
returns table (
  tracked_market_events_table_exists boolean,
  tracked_market_event_reactions_table_exists boolean,
  upsert_tracked_market_event_function_exists boolean,
  capture_tracked_market_event_reference_function_exists boolean,
  capture_tracked_market_event_reaction_anchor_function_exists boolean
)
language sql
stable
security invoker
as $$
  select
    to_regclass('public.tracked_market_events') is not null,
    to_regclass('public.tracked_market_event_reactions') is not null,
    to_regprocedure(
      'public.upsert_tracked_market_event(text,text,text,text,text,text,text,timestamptz,text,text,uuid)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_reference(uuid,numeric,timestamptz,text,bigint,text,text,text)'
    ) is not null,
    to_regprocedure(
      'public.capture_tracked_market_event_reaction_anchor(uuid,timestamptz,text)'
    ) is not null;
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;
