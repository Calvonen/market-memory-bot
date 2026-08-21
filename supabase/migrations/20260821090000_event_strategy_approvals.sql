-- Audit trail for the strategy-draft preview -> approve -> persist flow.
-- Insert-only: records the approval act itself (who/what, when, which draft
-- fingerprint, which base expectation version it was checked against), kept
-- separate from event_expectation_versions which only records the resulting
-- snapshot, not the approval that produced it.
create table if not exists public.event_strategy_approvals (
  id uuid primary key default gen_random_uuid(),
  event_id text not null references public.market_events(event_id) on delete cascade,
  expectation_version integer not null,
  base_expectation_version integer not null,
  draft_fingerprint text not null,
  approved_by text not null,
  approved_via text not null,
  change_note text not null,
  created_at timestamptz not null default now()
);

alter table public.event_strategy_approvals enable row level security;
revoke all on table public.event_strategy_approvals from anon, authenticated;
grant select, insert on table public.event_strategy_approvals to service_role;

create index if not exists event_strategy_approvals_event_id_idx
  on public.event_strategy_approvals(event_id, created_at desc);

-- Atomic CAS-and-persist for the strategy-draft approve endpoint. The
-- Python read-then-write that used to check "current version ==
-- base_expectation_version" and then call the repository's own
-- max(version)+1-with-retry save() left a race: two concurrent approvals
-- against the same base version could both pass the Python check before
-- either wrote, so both could succeed as N+1 and N+2 instead of exactly one
-- winning and the other conflicting. This function makes the whole
-- check-and-write one statement, with the version check, the new
-- expectation-version insert, and the audit-trail insert in a single
-- transaction: either all three happen or none do, and pg_advisory_xact_lock
-- (the same per-event-serialization pattern used by the paper-run RPCs
-- above) forces concurrent callers for the same event through this section
-- one at a time, so the loser's version check runs against the winner's
-- already-committed version and correctly fails instead of racing it.
--
-- Structured fields (consensus/triggers/important_kpis/bull_case/base_case/
-- bear_case/invalidation_conditions) are jsonb here, matching every other
-- structured column in this schema (see event_paper_trade_runs.strategy/
-- risk/paper_order/completed_components above) - adjust these parameter
-- types if event_expectation_versions' real column types ever diverge from
-- that convention.
create or replace function public.approve_strategy_draft(
  input_event_id text,
  input_expected_base_version integer,
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
  input_change_note text,
  input_draft_fingerprint text,
  input_approved_by text,
  input_approved_via text
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
  -- Transaction-scoped: released automatically on commit or rollback, so a
  -- failure anywhere below never leaves the event locked for later callers.
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));

  select version into current_version
  from public.event_expectation_versions
  where event_id = input_event_id
  order by version desc
  limit 1;

  if current_version is null then
    raise exception 'event_not_found: %', input_event_id using errcode = 'P0002';
  end if;

  if current_version <> input_expected_base_version then
    raise exception 'expectation_version_conflict: expected % but current is %',
      input_expected_base_version, current_version using errcode = 'P0001';
  end if;

  next_version := input_expected_base_version + 1;
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

  -- Same transaction as the insert above: if this fails, the entire
  -- function raises and the expectation-version insert rolls back with it -
  -- an event is never left with a persisted version that has no audit row.
  insert into public.event_strategy_approvals (
    event_id, expectation_version, base_expectation_version, draft_fingerprint,
    approved_by, approved_via, change_note, created_at
  ) values (
    input_event_id, next_version, input_expected_base_version, input_draft_fingerprint,
    input_approved_by, input_approved_via, input_change_note, inserted_at
  );

  return query select next_version, inserted_at;
end;
$$;

revoke all on function public.approve_strategy_draft(
  text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text
) from public;
grant execute on function public.approve_strategy_draft(
  text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text
) to service_role;
