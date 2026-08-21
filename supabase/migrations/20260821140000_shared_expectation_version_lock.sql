-- The admin direct-write endpoint (POST /api/v1/events/{id}/expectation-versions)
-- used to allocate the next event_expectation_versions row with its own
-- unlocked "select max(version) then insert, retry on 23505" dance,
-- entirely independent of the pg_advisory_xact_lock the strategy-draft
-- approve_strategy_draft() function (see
-- 20260821090000_event_strategy_approvals.sql) takes for the very same
-- table. A concurrent admin write and a strategy-draft approval could
-- therefore genuinely race each other: both could read the same "current"
-- version before either wrote, and whichever lost the resulting
-- unique-constraint race surfaced as a raw, unmapped database error instead
-- of a controlled conflict.
--
-- This function gives the admin write path the same lock, on the same key,
-- as approve_strategy_draft() - hashtextextended(event_id, 1) - so the two
-- writers of event_expectation_versions can never interleave their
-- read-then-insert steps: whichever acquires the lock first fully commits
-- before the other proceeds, and the second writer's read then reflects
-- the first writer's result.
--
-- Unlike approve_strategy_draft(), this function has no caller-supplied
-- expected base version to check - the admin endpoint has never offered
-- optimistic-concurrency semantics, it simply writes on top of whatever is
-- current - so it always succeeds once it holds the lock; there is no
-- separate conflict path here to consider.
--
-- input_source_name/input_source_url/input_source_as_of/input_consensus/
-- input_important_kpis/input_bull_case/input_base_case/input_bear_case/
-- input_triggers/input_invalidation_conditions are all a *partial patch*:
-- SQL NULL on any of them means "leave this field as it currently is,"
-- exactly like the admin endpoint's own optional request fields. This
-- function resolves that patch itself, against a row it reads *after*
-- acquiring the lock above - not against whatever the caller already had
-- in hand before calling this function. The caller used to read "current"
-- via its own separate, unlocked query, merge the patch into a fully
-- resolved row in application code, and only then call this function with
-- every field already filled in - so a concurrent approve_strategy_draft()
-- call that committed in the window between that read and this insert
-- would have any field the admin didn't touch silently reverted back to
-- its pre-approval value. Reading "current" here, after the lock, is what
-- closes that window: the two writers can still never interleave their
-- read-then-insert steps, and now neither of them can act on a value read
-- before the lock was held either.
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
returns table (new_version integer, created_at timestamptz)
language plpgsql
security invoker
set search_path = public
as $$
declare
  current_row public.event_expectation_versions%rowtype;
  next_version integer;
  inserted_at timestamptz;
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

  next_version := current_row.version + 1;
  inserted_at := clock_timestamp();

  insert into public.event_expectation_versions (
    event_id, version, source_name, source_url, source_as_of,
    consensus, important_kpis, bull_case, base_case, bear_case,
    triggers, invalidation_conditions, change_note, created_at
  ) values (
    input_event_id,
    next_version,
    -- 'manual' is the same fallback the old Python-side merge applied
    -- (expectation.source_name or "manual") for the case where neither
    -- the patch nor the current row has ever had a source_name - kept
    -- here so this behavior change is scoped only to *when* the merge
    -- happens, not what it produces.
    coalesce(input_source_name, current_row.source_name, 'manual'),
    coalesce(input_source_url, current_row.source_url),
    coalesce(input_source_as_of, current_row.source_as_of),
    coalesce(input_consensus, current_row.consensus),
    coalesce(input_important_kpis, current_row.important_kpis),
    coalesce(input_bull_case, current_row.bull_case),
    coalesce(input_base_case, current_row.base_case),
    coalesce(input_bear_case, current_row.bear_case),
    coalesce(input_triggers, current_row.triggers),
    coalesce(input_invalidation_conditions, current_row.invalidation_conditions),
    input_change_note,
    inserted_at
  );

  return query select next_version, inserted_at;
end;
$$;

revoke all on function public.insert_next_expectation_version(
  text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text
) from public;
grant execute on function public.insert_next_expectation_version(
  text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text
) to service_role;
