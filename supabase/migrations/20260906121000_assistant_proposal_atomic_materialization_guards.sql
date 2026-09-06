-- Atomic guards for assistant-proposal materialization and re-review.
--
-- Two races are closed here:
-- 1. An already-existing tracked event must not be mutated by a proposal whose
--    reviewed expectation version is stale. The version check and event upsert
--    share the same event advisory lock and database transaction.
-- 2. Re-opening a proposal for review must not race a strategy approval. Both
--    operations lock the same assistant_event_proposals row before deciding.

begin;

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

  return query
  select *
  from public.approve_strategy_draft(
    input_event_id,
    input_expected_base_version,
    input_source_name,
    input_source_url,
    input_source_as_of,
    input_consensus,
    input_important_kpis,
    input_bull_case,
    input_base_case,
    input_bear_case,
    input_triggers,
    input_invalidation_conditions,
    input_change_note,
    input_draft_fingerprint,
    input_approved_by,
    input_approved_via
  );
end;
$$;

revoke all on function public.approve_assistant_proposal_strategy_draft(
  uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text
) from public, anon, authenticated;
grant execute on function public.approve_assistant_proposal_strategy_draft(
  uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text
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

commit;
