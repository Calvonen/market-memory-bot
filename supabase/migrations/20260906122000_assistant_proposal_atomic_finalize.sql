-- Finalize all review-dependent assistant writes in one transaction.
--
-- The earlier proposal-locked source setter still left a legal gap after its
-- transaction committed and before strategy approval began. Re-review could
-- acquire the proposal row in that gap. Replace the split surface with one
-- finalizer that owns the proposal row lock while it verifies/sets the official
-- source, performs the expectation CAS, records the approval receipt, and marks
-- the proposal materialized. Any failure rolls every one of those writes back.

begin;

-- Remove the split assistant-only source-write surface. Official-source writes
-- for assistant proposals are now reachable only inside the atomic finalizer.
drop function if exists public.set_assistant_event_official_release_source_approved(
  uuid, text, text, text, text, integer, text
);

-- Replace the previous assistant approval signature with the atomic finalizer.
drop function if exists public.approve_assistant_proposal_strategy_draft(
  uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb,
  text, text, text, text
);

create function public.approve_assistant_proposal_strategy_draft(
  input_proposal_id uuid,
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
  input_approved_via text,
  input_official_source_kind text,
  input_official_source_url text,
  input_official_source_title text,
  input_official_source_expected_version integer,
  input_official_source_needs_set boolean,
  input_official_source_actor text
)
returns table (new_version integer, created_at timestamptz)
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  proposal_row public.assistant_event_proposals%rowtype;
  source_state record;
  written_source record;
  current_version integer;
  next_version integer;
  inserted_at timestamptz;
  expected_source_actor text;
begin
  select * into proposal_row
  from public.assistant_event_proposals
  where id = input_proposal_id
  for update;

  if proposal_row.id is null then
    raise exception 'assistant_proposal_not_found' using errcode = 'P0002';
  end if;
  if proposal_row.status <> 'approved_for_materialization' then
    raise exception 'assistant_proposal_not_approved_for_materialization' using errcode = '55000';
  end if;
  if input_approved_via is distinct from ('assistant_proposal:' || input_proposal_id::text) then
    raise exception 'assistant_proposal_approval_via_conflict' using errcode = '55000';
  end if;
  if nullif(btrim(input_approved_by), '') is null
     or input_approved_by is distinct from proposal_row.reviewed_by then
    raise exception 'assistant_proposal_reviewer_conflict' using errcode = '55000';
  end if;

  expected_source_actor := 'assistant_proposal:' || input_proposal_id::text ||
    ':reviewer:' || proposal_row.reviewed_by;
  if input_official_source_actor is distinct from expected_source_actor then
    raise exception 'assistant_proposal_source_actor_conflict' using errcode = '55000';
  end if;

  -- Serialize with every expectation writer before making the official-source
  -- mutation. If the reviewed expectation version is stale, no source write is
  -- attempted at all.
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

  if input_official_source_needs_set then
    select * into written_source
    from public.set_event_official_release_source_approved(
      input_event_id,
      input_official_source_kind,
      input_official_source_url,
      input_official_source_title,
      input_official_source_expected_version,
      input_official_source_actor
    );

    if written_source.out_event_id is distinct from input_event_id
       or written_source.out_source_kind is distinct from input_official_source_kind
       or written_source.out_source_url is distinct from input_official_source_url
       or written_source.out_source_title is distinct from input_official_source_title then
      raise exception 'assistant_proposal_official_source_write_identity_conflict' using errcode = '55000';
    end if;
  else
    -- The Python pre-read may be stale. Re-read the audited canonical state
    -- inside this transaction and require the exact reviewed source to still be
    -- active before strategy approval can commit.
    select * into source_state
    from public.get_audited_official_release_source_state(input_event_id);

    if source_state.out_is_active is distinct from true
       or source_state.out_version is distinct from input_official_source_expected_version
       or source_state.out_source_kind is distinct from input_official_source_kind
       or source_state.out_source_url is distinct from input_official_source_url
       or source_state.out_source_title is distinct from input_official_source_title then
      raise exception 'assistant_proposal_official_source_state_conflict' using errcode = '40001';
    end if;
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

  insert into public.event_strategy_approvals (
    event_id, expectation_version, base_expectation_version, draft_fingerprint,
    approved_by, approved_via, change_note, created_at
  ) values (
    input_event_id, next_version, input_expected_base_version, input_draft_fingerprint,
    input_approved_by, input_approved_via, input_change_note, inserted_at
  );

  update public.assistant_event_proposals
  set status = 'materialized'
  where id = input_proposal_id
    and status = 'approved_for_materialization';

  if not found then
    raise exception 'assistant_proposal_materialized_status_cas_failed' using errcode = '55000';
  end if;

  return query select next_version, inserted_at;
end;
$$;

revoke all on function public.approve_assistant_proposal_strategy_draft(
  uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb,
  text, text, text, text, text, text, text, integer, boolean, text
) from public, anon, authenticated;
grant execute on function public.approve_assistant_proposal_strategy_draft(
  uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb,
  text, text, text, text, text, text, text, integer, boolean, text
) to service_role;

-- This migration changes the assistant strategy-write contract again. Advance
-- the deterministic marker so deploy verification cannot accept the split
-- source/approval implementation from the previous migration.
create or replace function public.strategy_draft_schema_version()
returns integer
language sql
immutable
security invoker
set search_path = public
as $$
  select 3;
$$;

revoke all on function public.strategy_draft_schema_version() from public;
grant execute on function public.strategy_draft_schema_version() to service_role;

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
    ) is not null
      and to_regprocedure(
        'public.approve_assistant_proposal_strategy_draft(uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text, text, text, text, integer, boolean, text)'
      ) is not null,
    to_regprocedure(
      'public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)'
    ) is not null,
    public.strategy_draft_schema_version() = 3;
$$;

revoke all on function public.verify_strategy_draft_schema() from public;
grant execute on function public.verify_strategy_draft_schema() to service_role;

commit;
