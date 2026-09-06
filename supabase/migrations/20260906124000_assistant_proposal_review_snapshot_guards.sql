-- Bind every review-dependent assistant write to one immutable review round.
-- Also hold the official-source advisory lock through finalization and advance
-- the deterministic strategy schema gate past the receipt-ACL migration.

begin;

-- Replace the assistant canonical-event boundary with a review-snapshot-aware
-- signature. A stale materializer call from review N must not be able to write
-- after the same proposal has been reopened and approved as review N+1.
drop function if exists public.upsert_assistant_proposal_canonical_tracked_event(
  uuid, integer, text, text, text, text, text, text, text, timestamptz, date, text, text, text
);

create function public.upsert_assistant_proposal_canonical_tracked_event(
  input_proposal_id uuid,
  input_expected_review_round integer,
  input_expected_base_version integer,
  input_company_name text,
  input_instrument text,
  input_market text,
  input_source text,
  input_external_key text,
  input_kind text,
  input_title text,
  input_event_at timestamptz,
  input_event_date date,
  input_event_time_status text,
  input_actor text,
  input_expected_tracked_instrument_id text
)
returns table (
  out_id uuid,
  out_tracked_instrument_id text,
  out_calendar_event_id uuid,
  out_company_name text,
  out_instrument text,
  out_market text,
  out_source text,
  out_external_key text,
  out_kind text,
  out_title text,
  out_event_at timestamptz,
  out_event_date date,
  out_event_time_status text,
  out_status text,
  out_reference_price numeric,
  out_reference_captured_at timestamptz,
  out_created_by text,
  out_updated_by text,
  out_created_at timestamptz,
  out_updated_at timestamptz,
  out_action text
)
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  proposal_row public.assistant_event_proposals%rowtype;
  existing_event public.tracked_market_events%rowtype;
  existing_event_count integer;
  current_version integer;
begin
  if input_expected_review_round is null or input_expected_review_round < 1 then
    raise exception 'assistant_proposal_expected_review_round_invalid' using errcode = '22023';
  end if;
  if input_expected_base_version is null or input_expected_base_version < 1 then
    raise exception 'assistant_proposal_expected_base_version_invalid' using errcode = '22023';
  end if;

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
  if proposal_row.review_round is distinct from input_expected_review_round then
    raise exception 'assistant_proposal_review_round_conflict: expected % but current is %',
      input_expected_review_round, proposal_row.review_round using errcode = '40001';
  end if;
  if proposal_row.proposal_key is distinct from input_external_key then
    raise exception 'assistant_proposal_external_key_conflict' using errcode = '55000';
  end if;

  -- Same identity lock as every generic non-calendar canonical producer.
  perform pg_advisory_xact_lock(
    hashtextextended(
      coalesce(input_source, '<null>') || chr(31) || coalesce(input_external_key, '<null>'),
      3
    )
  );

  select count(*) into existing_event_count
  from public.tracked_market_events
  where source = input_source
    and external_key = input_external_key;

  if existing_event_count > 1 then
    raise exception 'assistant_proposal_multiple_canonical_events' using errcode = '55000';
  end if;

  if existing_event_count = 1 then
    select * into existing_event
    from public.tracked_market_events
    where source = input_source
      and external_key = input_external_key
    for update;

    perform pg_advisory_xact_lock(
      hashtextextended('tracked:' || existing_event.id::text, 1)
    );

    select version into current_version
    from public.event_expectation_versions
    where event_id = 'tracked:' || existing_event.id::text
    order by version desc
    limit 1;

    if current_version is not null
       and current_version <> input_expected_base_version then
      raise exception 'expectation_version_conflict: expected % but current is %',
        input_expected_base_version, current_version using errcode = 'P0001';
    end if;
  end if;

  return query
  select *
  from public.upsert_canonical_tracked_market_event(
    input_company_name,
    input_instrument,
    input_market,
    input_source,
    input_external_key,
    input_kind,
    input_title,
    input_event_at,
    input_event_date,
    input_event_time_status,
    input_actor,
    null,
    input_expected_tracked_instrument_id
  );
end;
$$;

revoke all on function public.upsert_assistant_proposal_canonical_tracked_event(
  uuid, integer, integer, text, text, text, text, text, text, text, timestamptz, date, text, text, text
) from public, anon, authenticated;
grant execute on function public.upsert_assistant_proposal_canonical_tracked_event(
  uuid, integer, integer, text, text, text, text, text, text, text, timestamptz, date, text, text, text
) to service_role;

-- Replace the atomic finalizer with the same review snapshot token. It also
-- always acquires the canonical official-source lock (salt 2) after the
-- expectation lock (salt 1), even when the source already matched the Python
-- pre-read. This keeps source equality true through strategy approval/commit.
drop function if exists public.approve_assistant_proposal_strategy_draft(
  uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb,
  text, text, text, text, text, text, text, integer, boolean, text
);

create function public.approve_assistant_proposal_strategy_draft(
  input_proposal_id uuid,
  input_expected_review_round integer,
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
security definer
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
  if input_expected_review_round is null or input_expected_review_round < 1 then
    raise exception 'assistant_proposal_expected_review_round_invalid' using errcode = '22023';
  end if;

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
  if proposal_row.review_round is distinct from input_expected_review_round then
    raise exception 'assistant_proposal_review_round_conflict: expected % but current is %',
      input_expected_review_round, proposal_row.review_round using errcode = '40001';
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

  -- Global lock order for this finalizer: proposal row -> expectation(salt 1)
  -- -> official source(salt 2). Re-review contends on the proposal row, every
  -- expectation writer contends on salt 1, and source writers contend on salt 2.
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

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 2));

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
    and status = 'approved_for_materialization'
    and review_round = input_expected_review_round;

  if not found then
    raise exception 'assistant_proposal_materialized_status_cas_failed' using errcode = '55000';
  end if;

  return query select next_version, inserted_at;
end;
$$;

revoke all on function public.approve_assistant_proposal_strategy_draft(
  uuid, integer, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb,
  text, text, text, text, text, text, text, integer, boolean, text
) from public, anon, authenticated;
grant execute on function public.approve_assistant_proposal_strategy_draft(
  uuid, integer, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb,
  text, text, text, text, text, text, text, integer, boolean, text
) to service_role;

-- Re-assert the receipt-table ACL in the same migration as the schema marker.
-- A deployment that stops before this migration cannot report schema v4.
revoke insert on table public.event_strategy_approvals from service_role;
grant select on table public.event_strategy_approvals to service_role;

create or replace function public.strategy_draft_schema_version()
returns integer
language sql
immutable
security invoker
set search_path = public
as $$
  select 4;
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
    to_regclass('public.event_strategy_approvals') is not null
      and not has_table_privilege('service_role', 'public.event_strategy_approvals', 'INSERT'),
    to_regprocedure(
      'public.approve_strategy_draft(text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text)'
    ) is not null
      and to_regprocedure(
        'public.approve_assistant_proposal_strategy_draft(uuid, integer, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text, text, text, text, integer, boolean, text)'
      ) is not null,
    to_regprocedure(
      'public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)'
    ) is not null,
    public.strategy_draft_schema_version() = 4;
$$;

revoke all on function public.verify_strategy_draft_schema() from public;
grant execute on function public.verify_strategy_draft_schema() to service_role;

commit;
