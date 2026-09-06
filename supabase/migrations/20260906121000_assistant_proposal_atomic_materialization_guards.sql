-- Atomic guards for assistant-proposal materialization and re-review.
--
-- This migration closes every reviewed-proposal race at the database boundary:
-- 1. Canonical tracked-event identity creation is serialized for every generic
--    producer before an assistant decides whether the event already exists.
-- 2. Existing assistant events are version-gated under the same expectation
--    advisory lock used by every expectation writer before canonical mutation.
-- 3. The assistant_proposal:* approval-receipt namespace is reserved from the
--    generic strategy approval RPC. Assistant approvals lock the proposal row
--    and perform the strategy CAS/audit insert in that same transaction.
-- 4. Re-review and official-source approval lock the same proposal row, so an
--    old review cannot commit an approved source after the proposal is reopened.

begin;

-- Every non-calendar canonical producer ultimately passes through this runtime
-- writer. Give (source, external_key) one shared transaction lock so absence
-- checks and conflict-path updates cannot race a first insert by another
-- producer. Calendar promotion deliberately remains on its separate reviewed
-- calendar-first lock order and private compatibility writer.
create or replace function public.upsert_tracked_market_event(
  input_company_name text,
  input_instrument text,
  input_market text,
  input_source text,
  input_external_key text,
  input_kind text,
  input_title text,
  input_event_at timestamptz,
  input_event_time_status text,
  input_actor text,
  input_calendar_event_id uuid default null
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
security definer
set search_path = pg_catalog, public
as $$
begin
  if input_calendar_event_id is not null then
    raise exception 'tracked_market_event_calendar_binding_forbidden';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended(
      coalesce(input_source, '<null>') || chr(31) || coalesce(input_external_key, '<null>'),
      3
    )
  );

  return query
  select *
  from public.upsert_tracked_market_event_calendar_compat_v11(
    input_company_name,
    input_instrument,
    input_market,
    input_source,
    input_external_key,
    input_kind,
    input_title,
    input_event_at,
    input_event_time_status,
    input_actor,
    null
  );
end;
$$;

revoke all on function public.upsert_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, text, text, uuid
) from public, anon, authenticated;
grant execute on function public.upsert_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, text, text, uuid
) to service_role;

create or replace function public.upsert_assistant_proposal_canonical_tracked_event(
  input_proposal_id uuid,
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
  if proposal_row.proposal_key is distinct from input_external_key then
    raise exception 'assistant_proposal_external_key_conflict' using errcode = '55000';
  end if;

  -- Same identity lock as the generic tracked-event writer above, acquired
  -- before deciding whether the key is new. Any producer trying to create or
  -- update this canonical key must wait until this transaction commits/rolls
  -- back, so the absence branch cannot turn into a conflict-path update later.
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
  uuid, integer, text, text, text, text, text, text, text, timestamptz, date, text, text, text
) from public, anon, authenticated;
grant execute on function public.upsert_assistant_proposal_canonical_tracked_event(
  uuid, integer, text, text, text, text, text, text, text, timestamptz, date, text, text, text
) to service_role;

-- Reserve the assistant receipt namespace at the actual generic database write
-- boundary. API validation alone would not protect direct repository/RPC users.
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
set search_path = pg_catalog, public
as $$
declare
  current_version integer;
  next_version integer;
  inserted_at timestamptz;
begin
  if input_approved_via like 'assistant_proposal:%' then
    raise exception 'assistant_proposal_approval_namespace_reserved' using errcode = '42501';
  end if;

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
) from public, anon, authenticated;
grant execute on function public.approve_strategy_draft(
  text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text
) to service_role;

create or replace function public.approve_assistant_proposal_strategy_draft(
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
  input_approved_via text
)
returns table (new_version integer, created_at timestamptz)
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  proposal_row public.assistant_event_proposals%rowtype;
  current_version integer;
  next_version integer;
  inserted_at timestamptz;
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

  -- Same per-event lock and CAS contract as the generic approval writer, but
  -- retained under the proposal-row lock so re-review cannot interleave.
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

revoke all on function public.approve_assistant_proposal_strategy_draft(
  uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text
) from public, anon, authenticated;
grant execute on function public.approve_assistant_proposal_strategy_draft(
  uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text
) to service_role;

create or replace function public.set_assistant_event_official_release_source_approved(
  input_proposal_id uuid,
  input_event_id text,
  input_source_kind text,
  input_source_url text,
  input_source_title text,
  input_expected_version integer,
  input_actor text
)
returns table (
  out_event_id text,
  out_source_kind text,
  out_source_url text,
  out_source_title text,
  out_version integer,
  out_created_at timestamptz,
  out_updated_at timestamptz
)
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  proposal_row public.assistant_event_proposals%rowtype;
  expected_actor text;
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
  if proposal_row.reviewed_by is null or btrim(proposal_row.reviewed_by) = '' then
    raise exception 'assistant_proposal_reviewer_missing' using errcode = '55000';
  end if;

  expected_actor := 'assistant_proposal:' || input_proposal_id::text ||
    ':reviewer:' || proposal_row.reviewed_by;
  if input_actor is distinct from expected_actor then
    raise exception 'assistant_proposal_source_actor_conflict' using errcode = '55000';
  end if;

  -- The proposal row remains locked while the existing approved source CAS and
  -- its audit insert run in this transaction. Re-review therefore either wins
  -- before this check (and source approval fails) or waits for this approved
  -- write to commit before deciding whether the proposal can be reopened.
  return query
  select *
  from public.set_event_official_release_source_approved(
    input_event_id,
    input_source_kind,
    input_source_url,
    input_source_title,
    input_expected_version,
    input_actor
  );
end;
$$;

revoke all on function public.set_assistant_event_official_release_source_approved(
  uuid, text, text, text, text, integer, text
) from public, anon, authenticated;
grant execute on function public.set_assistant_event_official_release_source_approved(
  uuid, text, text, text, text, integer, text
) to service_role;

create or replace function public.reopen_assistant_event_proposal_for_review(
  input_proposal_id uuid
)
returns table (out_status text)
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  proposal_row public.assistant_event_proposals%rowtype;
  expected_approval_via text;
begin
  select * into proposal_row
  from public.assistant_event_proposals
  where id = input_proposal_id
  for update;

  if proposal_row.id is null then
    raise exception 'assistant_proposal_not_found' using errcode = 'P0002';
  end if;
  if proposal_row.status <> 'approved_for_materialization' then
    raise exception 'assistant_proposal_not_reopenable' using errcode = '55000';
  end if;

  expected_approval_via := 'assistant_proposal:' || input_proposal_id::text;
  if exists (
    select 1
    from public.event_strategy_approvals esa
    where esa.approved_via = expected_approval_via
  ) then
    raise exception 'assistant_proposal_already_has_strategy_approval' using errcode = '55000';
  end if;

  update public.assistant_event_proposals
  set status = 'draft'
  where id = input_proposal_id
    and status = 'approved_for_materialization';

  if not found then
    raise exception 'assistant_proposal_rereview_status_cas_failed' using errcode = '55000';
  end if;

  return query select 'draft'::text;
end;
$$;

revoke all on function public.reopen_assistant_event_proposal_for_review(uuid)
  from public, anon, authenticated;
grant execute on function public.reopen_assistant_event_proposal_for_review(uuid)
  to service_role;

-- approve_strategy_draft() changed semantics in this migration. Bump the
-- deterministic schema marker so deploy gates cannot accept an older DB body
-- merely because the RPC signature still exists.
create or replace function public.strategy_draft_schema_version()
returns integer
language sql
immutable
security invoker
set search_path = public
as $$
  select 2;
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
        'public.approve_assistant_proposal_strategy_draft(uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text)'
      ) is not null,
    to_regprocedure(
      'public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)'
    ) is not null,
    public.strategy_draft_schema_version() = 2;
$$;

revoke all on function public.verify_strategy_draft_schema() from public;
grant execute on function public.verify_strategy_draft_schema() to service_role;

commit;
