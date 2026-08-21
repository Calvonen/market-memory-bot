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
  current_version integer;
  next_version integer;
  inserted_at timestamptz;
begin
  -- Same lock, same salt, as approve_strategy_draft(): the two functions
  -- contend on the identical advisory lock for a given event_id.
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));

  select version into current_version
  from public.event_expectation_versions
  where event_id = input_event_id
  order by version desc
  limit 1;

  next_version := coalesce(current_version, 0) + 1;
  inserted_at := clock_timestamp();

  insert into public.event_expectation_versions (
    event_id, version, source_name, source_url, source_as_of,
    consensus, important_kpis, bull_case, base_case, bear_case,
    triggers, invalidation_conditions, change_note, created_at
  ) values (
    input_event_id, next_version, input_source_name, input_source_url, input_source_as_of,
    input_consensus, input_important_kpis, input_bull_case, input_base_case, input_bear_case,
    input_triggers, input_invalidation_conditions, input_change_note, inserted_at
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
