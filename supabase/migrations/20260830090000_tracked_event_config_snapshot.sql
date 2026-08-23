-- Whole file runs as one transaction: the validator function, the CHECK
-- constraint that depends on it, the immutability trigger, and the RPC all
-- need to land together or not at all - a partial apply (e.g. the RPC
-- updated to call a validator that a failed statement never created) would
-- leave the database in a state none of these objects were designed for.
begin;

alter table public.tracked_market_events
  add column if not exists tracking_config_snapshot jsonb;

comment on column public.tracked_market_events.tracking_config_snapshot is
  'Immutable snapshot of the effective reaction-monitoring settings used for this tracked event. Null for legacy/not-yet-started events.';

-- Single source of truth for "is this a well-formed tracking_config_snapshot
-- v1 payload", shared by the capture RPC (early, friendly rejection) and the
-- table CHECK constraint below (the actual enforcement boundary - it applies
-- to every write path, not just the RPC). Keep this in exact field-for-field
-- sync with trading_system/tracked_event_config.py's TrackedEventConfigSnapshot/
-- TrackedEventMonitoringStageSnapshot: schema_version is pinned to exactly 1 -
-- there is deliberately no forward-compatible "unknown version passes"
-- fallback, since a v2 snapshot shape must be a new validator function, not a
-- silently-accepted wider contract.
create or replace function public.is_valid_tracked_event_config_snapshot_v1(
  snapshot jsonb
)
returns boolean
language plpgsql
immutable
as $$
declare
  stage jsonb;
  stage_index integer := 0;
  stage_count integer;
  previous_start numeric;
  current_start numeric;
  current_interval numeric;
begin
  if snapshot is null or jsonb_typeof(snapshot) <> 'object' then
    return false;
  end if;

  if not (snapshot ? 'schema_version')
     or jsonb_typeof(snapshot -> 'schema_version') <> 'number'
     or (snapshot ->> 'schema_version')::numeric <> 1 then
    return false;
  end if;

  if not (snapshot ? 'monitor_hours')
     or jsonb_typeof(snapshot -> 'monitor_hours') <> 'number'
     or (snapshot ->> 'monitor_hours')::numeric <= 0 then
    return false;
  end if;

  if not (snapshot ? 'reference_lead_seconds')
     or jsonb_typeof(snapshot -> 'reference_lead_seconds') <> 'number'
     or (snapshot ->> 'reference_lead_seconds')::numeric <= 0 then
    return false;
  end if;

  if not (snapshot ? 'max_wait_for_market_hours')
     or jsonb_typeof(snapshot -> 'max_wait_for_market_hours') <> 'number'
     or (snapshot ->> 'max_wait_for_market_hours')::numeric <= 0 then
    return false;
  end if;

  if not (snapshot ? 'reaction_stages')
     or jsonb_typeof(snapshot -> 'reaction_stages') <> 'array' then
    return false;
  end if;

  stage_count := jsonb_array_length(snapshot -> 'reaction_stages');
  if stage_count = 0 then
    return false;
  end if;

  for stage in select value from jsonb_array_elements(snapshot -> 'reaction_stages') loop
    stage_index := stage_index + 1;

    if jsonb_typeof(stage) <> 'object' then
      return false;
    end if;
    if not (stage ? 'start_after_minutes')
       or jsonb_typeof(stage -> 'start_after_minutes') <> 'number' then
      return false;
    end if;
    if not (stage ? 'interval_minutes')
       or jsonb_typeof(stage -> 'interval_minutes') <> 'number' then
      return false;
    end if;

    current_start := (stage ->> 'start_after_minutes')::numeric;
    current_interval := (stage ->> 'interval_minutes')::numeric;

    if current_start < 0 then
      return false;
    end if;
    if current_interval not in (1, 5, 15) then
      return false;
    end if;
    if stage_index = 1 and current_start <> 0 then
      return false;
    end if;
    if stage_index > 1 and current_start <= previous_start then
      return false;
    end if;

    previous_start := current_start;
  end loop;

  return true;
end;
$$;

revoke all on function public.is_valid_tracked_event_config_snapshot_v1 from public;
grant execute on function public.is_valid_tracked_event_config_snapshot_v1 to service_role;

-- The actual enforcement boundary: applies to every write to this column,
-- including a direct `update tracked_market_events set
-- tracking_config_snapshot = ...` that bypasses the RPC entirely - the RPC's
-- own pre-check below is only a friendlier error message for its own callers,
-- not the thing that makes malformed data impossible.
alter table public.tracked_market_events
  drop constraint if exists tracked_market_events_tracking_config_snapshot_valid;

alter table public.tracked_market_events
  add constraint tracked_market_events_tracking_config_snapshot_valid
  check (
    tracking_config_snapshot is null
    or public.is_valid_tracked_event_config_snapshot_v1(tracking_config_snapshot)
  );

-- Immutability boundary, independent of the RPC: service_role has table-wide
-- UPDATE on tracked_market_events (see 20260827090000), so the RPC's own
-- capture-once row-lock logic is not, by itself, an enforcement mechanism -
-- anything running as service_role could still issue a direct UPDATE and
-- silently rewrite a previously-captured snapshot. This trigger makes that
-- impossible at the table level for every UPDATE, RPC-issued or not:
-- NULL -> valid is allowed (the RPC's own first capture goes through this),
-- identical -> identical is allowed (idempotent re-writes, including the
-- RPC's own NULL-guarded UPDATE branch and a resubmission via direct SQL),
-- and anything that changes an already-non-null snapshot's value (including
-- to NULL) is rejected. Schema validity of a first non-null write is not this
-- trigger's job - the CHECK constraint above already guarantees that for
-- every write, RPC or direct.
create or replace function public.enforce_tracked_market_event_config_snapshot_immutable()
returns trigger
language plpgsql
as $$
begin
  if OLD.tracking_config_snapshot is not null
     and NEW.tracking_config_snapshot is distinct from OLD.tracking_config_snapshot then
    raise exception 'tracked_market_event_config_snapshot_immutable';
  end if;
  return NEW;
end;
$$;

revoke all on function public.enforce_tracked_market_event_config_snapshot_immutable from public;
grant execute on function public.enforce_tracked_market_event_config_snapshot_immutable to service_role;

drop trigger if exists tracked_market_events_config_snapshot_immutable on public.tracked_market_events;

create trigger tracked_market_events_config_snapshot_immutable
  before update on public.tracked_market_events
  for each row
  execute function public.enforce_tracked_market_event_config_snapshot_immutable();

-- Mirrors the capture-once row-lock pattern already used by
-- capture_tracked_market_event_reference (20260827090000): lock the row
-- with `select ... for update` and branch explicitly on its current state,
-- rather than relying on a conditional UPDATE + FOUND to distinguish
-- "no such event" from "a different snapshot is already stored" - both of
-- those would otherwise silently return zero rows with no way for the
-- caller to tell them apart, and neither should ever overwrite an existing
-- different snapshot.
create or replace function public.capture_tracked_market_event_config_snapshot(
  input_event_id uuid,
  input_tracking_config_snapshot jsonb,
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
  if not public.is_valid_tracked_event_config_snapshot_v1(input_tracking_config_snapshot) then
    raise exception 'invalid_tracking_config_snapshot';
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

  if existing_row.tracking_config_snapshot is not null then
    if existing_row.tracking_config_snapshot = input_tracking_config_snapshot then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_config_snapshot_locked';
  end if;

  if existing_row.status not in ('tracked', 'monitoring') then
    raise exception 'tracked_market_event_not_snapshotable';
  end if;

  update public.tracked_market_events
  set tracking_config_snapshot = input_tracking_config_snapshot,
      updated_by = input_actor,
      updated_at = now()
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.capture_tracked_market_event_config_snapshot from public;
grant execute on function public.capture_tracked_market_event_config_snapshot to service_role;

-- Keep the pre-deploy schema gate (verify_tracked_event_runtime_schema(),
-- scripts/verify_supabase_schema.py) in lockstep with this migration, same
-- as every earlier tracked-event runtime migration: bump the version marker
-- and extend the verifier so a deploy against a database missing this RPC
-- fails closed instead of starting a worker that cannot capture snapshots.
create or replace function public.tracked_event_runtime_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 3;
$$;

revoke all on function public.tracked_event_runtime_schema_version from public;
grant execute on function public.tracked_event_runtime_schema_version to service_role;

-- The OUT signature changed in this migration, so PostgreSQL requires the old
-- verifier to be dropped before recreating it with the extra column.
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
    public.tracked_event_runtime_schema_version();
$$;

revoke all on function public.verify_tracked_event_runtime_schema from public;
grant execute on function public.verify_tracked_event_runtime_schema to service_role;

commit;
