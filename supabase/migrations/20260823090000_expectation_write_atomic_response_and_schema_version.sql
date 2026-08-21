-- Two fixes, bundled into one new migration because they are the same
-- kind of problem: insert_next_expectation_version()'s *signature* has
-- stayed the same across several rounds of changes to its actual body -
-- first to take the lock at all, then to resolve its partial patch
-- against a row read after acquiring that lock instead of one read
-- beforehand. Editing the function body in place (via `create or replace`
-- in the migration that first introduced it) worked for local iteration,
-- but it is not something a deploy gate can ever detect: to_regprocedure()
-- only reports whether a function with a given signature exists, not what
-- its body currently does. A Supabase project that had only the *original*
-- version of insert_next_expectation_version() applied - same signature,
-- old (buggy) semantics - would report every existing schema-gate check as
-- passing, and the backend would be restarted into production still
-- calling a function whose behavior no longer matches what the backend
-- code assumes.
--
-- Going forward, a semantic change to any function in this write path must
-- land as its own new migration (this one), and must bump
-- strategy_draft_schema_version() below - a single, explicit, deterministic
-- marker the schema gate checks by *value*, not just by the calling
-- function's existence. A same-signature RPC from an older migration can
-- never accidentally satisfy a newer required version.
create or replace function public.strategy_draft_schema_version()
returns integer
language sql
immutable
security invoker
set search_path = public
as $$
  select 1;
$$;

revoke all on function public.strategy_draft_schema_version() from public;
grant execute on function public.strategy_draft_schema_version() to service_role;

-- Fix: insert_next_expectation_version() used to return only
-- (new_version, created_at), so SupabaseEventExpectationRepository.
-- apply_partial_update() re-read the event via a separate get() call
-- after this function's transaction committed, to build the full
-- EventExpectation its caller needs back. That re-read is its own,
-- unlocked query - if a second writer (another admin patch, or a
-- strategy-draft approval) committed a *later* version for the same
-- event_id in the gap between this transaction committing and that
-- re-read running, the re-read would return the later writer's row, and
-- this call would incorrectly report someone else's version and fields
-- as if they were what this request itself just wrote.
--
-- Returning everything the caller needs - every merged field, plus the
-- event's own identity (instrument/event_name/scheduled_date, which live
-- on market_events, not on event_expectation_versions) - directly from
-- this function's own return row removes the need for that re-read
-- entirely. What is returned is *exactly* what this call's own insert
-- wrote (built from the same merged_* variables used in the insert
-- itself, not recomputed or re-read), never something a later, unrelated
-- write could have changed first.
--
-- Output columns are prefixed out_ so plpgsql's own #variable_conflict
-- resolution can never confuse a returned column (declared as an
-- assignable OUT parameter, which becomes a plpgsql variable in this
-- function's scope) with an actual column of the same bare name being
-- selected from event_expectation_versions/market_events within the
-- function body - out_consensus vs. the queried consensus column, etc.
create or replace function public.insert_next_expectation_version(
  input_event_id text,
  input_source_name text,
  input_source_url text,
  input_source_as_of date,
  input_consensus jsonb,
  input_important_kpis jsonb,
  input_bull_case jsonb,
  input_base_case jsonb,
  input_bear_case jsonb,
  input_triggers jsonb,
  input_invalidation_conditions jsonb,
  input_change_note text
)
returns table (
  out_version integer,
  out_created_at timestamptz,
  out_instrument text,
  out_event_name text,
  out_scheduled_date date,
  out_source_name text,
  out_source_url text,
  out_source_as_of date,
  out_consensus jsonb,
  out_important_kpis jsonb,
  out_bull_case jsonb,
  out_base_case jsonb,
  out_bear_case jsonb,
  out_triggers jsonb,
  out_invalidation_conditions jsonb
)
language plpgsql
security invoker
set search_path = public
as $$
declare
  current_row public.event_expectation_versions%rowtype;
  event_row public.market_events%rowtype;
  next_version integer;
  inserted_at timestamptz;
  merged_source_name text;
  merged_source_url text;
  merged_source_as_of date;
  merged_consensus jsonb;
  merged_important_kpis jsonb;
  merged_bull_case jsonb;
  merged_base_case jsonb;
  merged_bear_case jsonb;
  merged_triggers jsonb;
  merged_invalidation_conditions jsonb;
begin
  -- Same lock, same salt, as approve_strategy_draft(): the two functions
  -- contend on the identical advisory lock for a given event_id.
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));

  -- Read *after* acquiring the lock, not before - this is the row the
  -- partial patch below is resolved against, and it is only safe to treat
  -- as "current" because nothing else can write a new version for this
  -- event_id until this transaction releases the lock it just took.
  select *
  into current_row
  from public.event_expectation_versions
  where event_id = input_event_id
  order by version desc
  limit 1;

  if not found then
    raise exception 'event_not_found: %', input_event_id using errcode = 'P0002';
  end if;

  -- event_expectation_versions.event_id has a foreign key onto
  -- market_events(event_id), so a market_events row is guaranteed to
  -- exist here - current_row above could not exist otherwise.
  select * into event_row
  from public.market_events
  where event_id = input_event_id;

  next_version := current_row.version + 1;
  inserted_at := clock_timestamp();

  -- 'manual' is the same fallback the old Python-side merge applied
  -- (expectation.source_name or "manual") for the case where neither the
  -- patch nor the current row has ever had a source_name.
  merged_source_name := coalesce(input_source_name, current_row.source_name, 'manual');
  merged_source_url := coalesce(input_source_url, current_row.source_url);
  merged_source_as_of := coalesce(input_source_as_of, current_row.source_as_of);
  merged_consensus := coalesce(input_consensus, current_row.consensus);
  merged_important_kpis := coalesce(input_important_kpis, current_row.important_kpis);
  merged_bull_case := coalesce(input_bull_case, current_row.bull_case);
  merged_base_case := coalesce(input_base_case, current_row.base_case);
  merged_bear_case := coalesce(input_bear_case, current_row.bear_case);
  merged_triggers := coalesce(input_triggers, current_row.triggers);
  merged_invalidation_conditions :=
    coalesce(input_invalidation_conditions, current_row.invalidation_conditions);

  insert into public.event_expectation_versions (
    event_id, version, source_name, source_url, source_as_of,
    consensus, important_kpis, bull_case, base_case, bear_case,
    triggers, invalidation_conditions, change_note, created_at
  ) values (
    input_event_id, next_version, merged_source_name, merged_source_url, merged_source_as_of,
    merged_consensus, merged_important_kpis, merged_bull_case, merged_base_case, merged_bear_case,
    merged_triggers, merged_invalidation_conditions, input_change_note, inserted_at
  );

  -- Built entirely from this call's own local variables - never a fresh
  -- select against event_expectation_versions/market_events - so this is
  -- guaranteed to describe exactly what this call just wrote, regardless
  -- of what any other writer does immediately afterwards.
  return query select
    next_version,
    inserted_at,
    event_row.instrument,
    event_row.event_name,
    event_row.scheduled_date,
    merged_source_name,
    merged_source_url,
    merged_source_as_of,
    merged_consensus,
    merged_important_kpis,
    merged_bull_case,
    merged_base_case,
    merged_bear_case,
    merged_triggers,
    merged_invalidation_conditions;
end;
$$;

revoke all on function public.insert_next_expectation_version(
  text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text
) from public;
grant execute on function public.insert_next_expectation_version(
  text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text
) to service_role;

-- verify_strategy_draft_schema() now also reports whether
-- strategy_draft_schema_version() matches the exact version this
-- migration expects (1) - not just whether insert_next_expectation_version
-- exists under some signature. A Supabase project on an older migration
-- (pre-dating this file, or any future one that changes this function's
-- semantics again without bumping the marker) reports
-- schema_version_matches = false, or has no strategy_draft_schema_version()
-- to call at all - which PostgREST surfaces as this entire RPC call
-- failing, the same fail-closed path the rest of this function already
-- relies on for "hasn't been migrated yet."
create or replace function public.verify_strategy_draft_schema()
returns table (
  event_strategy_approvals_table_exists boolean,
  approve_strategy_draft_function_exists boolean,
  insert_next_expectation_version_function_exists boolean,
  schema_version_matches boolean
)
language sql
security invoker
set search_path = public
as $$
  select
    to_regclass('public.event_strategy_approvals') is not null,
    to_regprocedure(
      'public.approve_strategy_draft(text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text)'
    ) is not null,
    to_regprocedure(
      'public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)'
    ) is not null,
    public.strategy_draft_schema_version() = 1;
$$;

revoke all on function public.verify_strategy_draft_schema() from public;
grant execute on function public.verify_strategy_draft_schema() to service_role;
