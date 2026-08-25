-- Allow a pre-event context whose latest session is the event's own local
-- market date - but only through a canonical path that proves the session had
-- actually closed first.
--
-- The canonical selector (trading_system/session_date_resolver.py) picks the
-- pre-event pair by real exchange close timestamps against event_at, not by
-- session date against the event's date. An earnings release after its own
-- session's close therefore resolves to that same-day session as its latest
-- reference - its daily candle is complete and is the most recent market state
-- before the event. The base capture RPC predates that and rejects any snapshot
-- dated on or after the event's local date.
--
-- Simply relaxing the base RPC would extend the same-day allowance to every
-- service_role caller, including direct/legacy ones that never went through the
-- exchange-calendar close-time check - and a same-day session that has *not*
-- closed yet still has a forming Yahoo daily candle. So the base RPC keeps its
-- strict rule and a separate canonical RPC carries the relaxation behind an
-- explicit proof:
--
--   capture_tracked_market_event_pre_event_context           -> session_date < event local date
--   capture_tracked_market_event_pre_event_context_validated -> session_date <= event local date,
--                                                               given a verified session close
--
-- TRUST BOUNDARY - what the proof actually establishes:
--
--   The validated RPC requires the caller to state the exchange calendar's
--   close timestamp for the snapshot's own session, and then checks it against
--   facts the database owns rather than taking it on faith:
--
--     * input_session_close <= existing_row.event_at   (the row's own event_at)
--     * input_session_close <= clock_timestamp()       (the database clock)
--     * the close falls on snapshot_session_date when read in the market
--       timezone, so the proof cannot be an unrelated older timestamp
--
--   Those are exactly the two orderings that make a same-day snapshot sound:
--   the session closed before the event, and it has closed by now, so its daily
--   candle is complete rather than still forming. The only residue the database
--   still trusts is that the supplied timestamp really is that session's close -
--   PostgreSQL has no exchange calendar and cannot derive it. That residue is
--   owned by trading_system/session_calendar_adapter.py, which reads it from the
--   exchange calendar, and execute on both RPCs is revoked from PUBLIC and
--   granted only to service_role.
--
--   Everything else stays the database's authority and is unchanged: snapshot
--   structure and internal arithmetic (is_valid_pre_event_market_context_v1,
--   which also requires previous_session_date < session_date), the session never
--   being after the event, event lifecycle status, capture-once immutability
--   with exact-retry idempotency, and the event version and event_at deadline.
--
-- Keep the pre-deploy schema gate (verify_tracked_event_runtime_schema(),
-- scripts/verify_supabase_schema.py) in lockstep: the worker now requires the
-- validated RPC to exist, so it is added to the verifier and the runtime schema
-- version is bumped. The verifier can only see that a function exists, not which
-- body is deployed, so the version marker is also what stops a deploy against a
-- database still holding an older capture body.
begin;

-- Base capture RPC: unchanged pre-event-date rule, plus the session-ordering
-- assertions that were previously only implicit in the v1 validator.
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

  -- No close-time proof on this path, so a same-day session cannot be shown to
  -- have finished trading. Only strictly earlier sessions are accepted here;
  -- the validated RPC below is the one that can accept the event's own day.
  if snapshot_session_date >= event_local_date then
    raise exception 'pre_event_market_context_not_before_event';
  end if;

  if snapshot_previous_session_date >= snapshot_session_date then
    raise exception 'pre_event_market_context_sessions_out_of_order';
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

-- Canonical validated capture: compare-and-swap bound like the _if_current
-- wrapper, and additionally proof-bound on the snapshot session's close. This
-- is the only path that may accept a snapshot dated on the event's own day.
create or replace function public.capture_tracked_market_event_pre_event_context_validated(
  input_event_id uuid,
  input_pre_event_market_context jsonb,
  input_market_timezone text,
  input_actor text,
  input_expected_updated_at timestamptz,
  input_session_close timestamptz
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
  session_close_local_date date;
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
  if input_expected_updated_at is null then
    raise exception 'input_expected_updated_at is required';
  end if;
  if input_session_close is null then
    raise exception 'input_session_close is required';
  end if;

  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;

  -- Preserve exact retry idempotency if a valid capture committed but its
  -- response was lost. This branch creates no new snapshot, so it runs before
  -- the deadline, version and proof gates - a replay must not need to re-prove
  -- timing that was already accepted.
  if existing_row.pre_event_market_context = input_pre_event_market_context then
    return existing_row;
  end if;

  -- Preparation can run in a background thread that cannot be cancelled
  -- safely, so enforce the event deadline while the row is locked.
  if pg_catalog.clock_timestamp() >= existing_row.event_at then
    raise exception 'tracked_market_event_pre_event_context_deadline_passed';
  end if;

  if existing_row.updated_at is distinct from input_expected_updated_at then
    raise exception 'tracked_market_event_version_conflict';
  end if;

  begin
    event_local_date := (existing_row.event_at at time zone input_market_timezone)::date;
    snapshot_session_date := (input_pre_event_market_context ->> 'session_date')::date;
    snapshot_previous_session_date :=
      (input_pre_event_market_context ->> 'previous_session_date')::date;
    session_close_local_date := (input_session_close at time zone input_market_timezone)::date;
  exception when others then
    raise exception 'invalid_market_timezone_or_session_date';
  end;

  -- A session dated after the event's local market date can never precede the
  -- event, with or without a proof.
  if snapshot_session_date > event_local_date then
    raise exception 'pre_event_market_context_not_before_event';
  end if;

  if snapshot_previous_session_date >= snapshot_session_date then
    raise exception 'pre_event_market_context_sessions_out_of_order';
  end if;
  if snapshot_previous_session_date >= event_local_date then
    raise exception 'pre_event_market_context_not_before_event';
  end if;

  -- The supplied close must actually belong to the snapshot's own session, or
  -- an unrelated older timestamp could stand in as proof.
  if session_close_local_date <> snapshot_session_date then
    raise exception 'pre_event_market_context_session_close_mismatch';
  end if;

  -- The two orderings that make the snapshot sound: the session closed before
  -- the event, and it has closed by now, so its daily candle is complete.
  if input_session_close > existing_row.event_at then
    raise exception 'pre_event_market_context_session_not_closed_before_event';
  end if;
  if input_session_close > pg_catalog.clock_timestamp() then
    raise exception 'pre_event_market_context_session_not_closed_yet';
  end if;

  if existing_row.pre_event_market_context is not null then
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

revoke all on function public.capture_tracked_market_event_pre_event_context_validated(
  uuid, jsonb, text, text, timestamptz, timestamptz
) from public;
grant execute on function public.capture_tracked_market_event_pre_event_context_validated(
  uuid, jsonb, text, text, timestamptz, timestamptz
) to service_role;

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

-- The OUT signature gains the validated RPC, so the verifier must be dropped
-- before being recreated.
drop function if exists public.verify_tracked_event_runtime_schema();

create function public.verify_tracked_event_runtime_schema()
returns table (
  tracked_market_events_table_exists boolean,
  tracked_market_event_reactions_table_exists boolean,
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
  runtime_schema_version integer
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
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;

commit;
