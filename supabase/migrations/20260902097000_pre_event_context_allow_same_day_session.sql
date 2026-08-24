-- Allow a pre-event context whose latest session is the event's own local
-- market date, when that session had already closed before event_at.
--
-- The canonical selector (trading_system/session_date_resolver.py) picks the
-- pre-event pair by real exchange close timestamps against event_at, not by
-- session date against the event's date. An earnings release after its own
-- session's close therefore resolves to that same-day session as its latest
-- reference - its daily candle is complete and is the most recent market state
-- before the event. The original capture RPC predates that and rejects any
-- snapshot with session_date >= event local date, so it would reject exactly
-- the snapshots the fixed selector now produces.
--
-- TRUST BOUNDARY - read before tightening this back up:
--
--   PostgreSQL has no exchange calendar, so this function cannot know when a
--   session actually closed. It therefore cannot distinguish a legitimate
--   post-close same-day snapshot from a pre-close one. That timing proof is
--   owned by the canonical Python orchestration
--   (acquire_and_persist_pre_event_market_context_for_event), which resolves
--   the pair from exchange-calendar closes and additionally refuses to acquire
--   while either selected session is still open. Execute on this function is
--   revoked from PUBLIC and granted only to service_role, so that
--   orchestration is the only caller.
--
--   What the database remains the authority on is unchanged and still enforced
--   here: snapshot structure and internal arithmetic
--   (is_valid_pre_event_market_context_v1, which also requires
--   previous_session_date < session_date), the session never being *after* the
--   event, event lifecycle status, capture-once immutability with exact-retry
--   idempotency, and - in the _if_current wrapper - the event version and the
--   event_at deadline.
--
-- The only relaxation is >= becoming >: a session dated after the event's local
-- market date is still rejected, because no such session can precede the event
-- regardless of close time.
--
-- Keep the pre-deploy schema gate (verify_tracked_event_runtime_schema(),
-- scripts/verify_supabase_schema.py) in lockstep: the verifier can only see
-- that the function exists, not which body is deployed, so the runtime schema
-- version is bumped here. Without it a deploy against a database still holding
-- the stricter body would pass the gate and then reject every post-close
-- same-day capture at runtime.
begin;

create or replace function public.capture_tracked_market_event_pre_event_context(
  input_event_id uuid,
  input_pre_event_market_context jsonb,
  input_market_timezone text,
  input_actor text
)
returns public.tracked_market_events
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
  event_local_date date;
  snapshot_session_date date;
  snapshot_previous_session_date date;
begin
  if not public.is_valid_pre_event_market_context_v1(input_pre_event_market_context) then
    raise exception 'invalid_pre_event_market_context';
  end if;
  if nullif(btrim(input_market_timezone), '') is null then
    raise exception 'input_market_timezone is required';
  end if;
  if nullif(btrim(input_actor), '') is null then
    raise exception 'input_actor is required';
  end if;

  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;

  begin
    event_local_date := (existing_row.event_at at time zone input_market_timezone)::date;
    snapshot_session_date := (input_pre_event_market_context ->> 'session_date')::date;
    snapshot_previous_session_date :=
      (input_pre_event_market_context ->> 'previous_session_date')::date;
  exception when others then
    raise exception 'invalid_market_timezone_or_session_date';
  end;

  -- A session dated after the event's local market date can never precede the
  -- event. The same-day case is permitted: see the trust boundary above.
  if snapshot_session_date > event_local_date then
    raise exception 'pre_event_market_context_not_before_event';
  end if;

  -- Ordering is already required by is_valid_pre_event_market_context_v1;
  -- re-assert it here so the pair stays well-ordered even if that validator is
  -- ever relaxed, and so the previous session is strictly before the event.
  if snapshot_previous_session_date >= snapshot_session_date then
    raise exception 'pre_event_market_context_sessions_out_of_order';
  end if;
  if snapshot_previous_session_date >= event_local_date then
    raise exception 'pre_event_market_context_not_before_event';
  end if;

  if existing_row.pre_event_market_context is not null then
    if existing_row.pre_event_market_context = input_pre_event_market_context then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_pre_event_context_locked';
  end if;

  if existing_row.status not in ('tracked', 'monitoring') then
    raise exception 'tracked_market_event_not_context_captureable';
  end if;

  update public.tracked_market_events
  set pre_event_market_context = input_pre_event_market_context,
      updated_by = input_actor,
      updated_at = now()
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.capture_tracked_market_event_pre_event_context from public;
grant execute on function public.capture_tracked_market_event_pre_event_context to service_role;

create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 6;
$$;

revoke all on function public.tracked_event_runtime_schema_version from public;
grant execute on function public.tracked_event_runtime_schema_version to service_role;

commit;
