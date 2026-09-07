-- Close the remaining assistant-proposal provenance gaps at durable DB boundaries.
-- This migration supersedes the key/source inference introduced by the preceding
-- safety migration. Assistant origin is represented explicitly by a proposal ->
-- tracked-event binding, and finalization is checked against the immutable review
-- snapshot rather than caller-selected values.

begin;

create table if not exists public.assistant_proposal_materializations (
  proposal_id uuid primary key references public.assistant_event_proposals(id) on delete restrict,
  review_round integer not null check (review_round > 0),
  tracked_event_id uuid not null unique references public.tracked_market_events(id) on delete restrict,
  event_id text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (event_id = 'tracked:' || tracked_event_id::text)
);

alter table public.assistant_proposal_materializations enable row level security;
revoke all on table public.assistant_proposal_materializations from public, anon, authenticated;
grant select, insert, update on table public.assistant_proposal_materializations to service_role;

create or replace function public.assistant_normalize_text_array(input_value jsonb)
returns jsonb
language sql
immutable
strict
set search_path = pg_catalog
as $$
  select coalesce(jsonb_agg(to_jsonb(value) order by first_ord), '[]'::jsonb)
  from (
    select value, min(ord) as first_ord
    from (
      select btrim(elem.value) as value, elem.ordinality as ord
      from jsonb_array_elements_text(input_value) with ordinality as elem(value, ordinality)
    ) cleaned
    where value <> ''
    group by value
  ) deduped;
$$;

revoke all on function public.assistant_normalize_text_array(jsonb) from public, anon, authenticated;
grant execute on function public.assistant_normalize_text_array(jsonb) to service_role;

create or replace function public.upsert_assistant_proposal_canonical_tracked_event(
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
  review_row public.assistant_event_proposal_reviews%rowtype;
  existing_event public.tracked_market_events%rowtype;
  existing_event_count integer;
  current_version integer;
  proposal_event_at_text text;
  proposal_event_at timestamptz;
  proposal_local_event_at timestamp;
  proposal_event_time_status text;
  canonical_write record;
  bound_row public.assistant_proposal_materializations%rowtype;
begin
  if input_expected_review_round is null or input_expected_review_round < 1 then
    raise exception 'assistant_proposal_expected_review_round_invalid' using errcode = '22023';
  end if;
  if input_expected_base_version is null or input_expected_base_version < 1 then
    raise exception 'assistant_proposal_expected_base_version_invalid' using errcode = '22023';
  end if;
  if input_source is distinct from 'manual' then
    raise exception 'assistant_proposal_source_must_be_manual' using errcode = '55000';
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

  select * into review_row
  from public.assistant_event_proposal_reviews
  where proposal_id = input_proposal_id
    and review_round = input_expected_review_round
    and decision = 'approved_for_materialization';
  if not found then
    raise exception 'assistant_proposal_review_snapshot_missing' using errcode = '55000';
  end if;

  if review_row.proposal_key is distinct from proposal_row.proposal_key
     or review_row.payload is distinct from proposal_row.payload
     or review_row.requested_execution_mode is distinct from proposal_row.requested_execution_mode
     or review_row.reviewed_by is distinct from proposal_row.reviewed_by then
    raise exception 'assistant_proposal_review_snapshot_conflict' using errcode = '55000';
  end if;
  if review_row.requested_execution_mode <> 'demo' then
    raise exception 'assistant_proposal_live_locked' using errcode = '55000';
  end if;

  -- Bind every canonical identity field to the immutable reviewed payload.
  if review_row.proposal_key is distinct from input_external_key
     or nullif(btrim(review_row.payload ->> 'company_name'), '') is distinct from nullif(btrim(input_company_name), '')
     or upper(btrim(review_row.payload ->> 'instrument')) is distinct from upper(btrim(input_instrument))
     or nullif(btrim(review_row.payload ->> 'market'), '') is distinct from nullif(btrim(input_market), '')
     or review_row.payload ->> 'kind' is distinct from input_kind
     or nullif(btrim(review_row.payload ->> 'title'), '') is distinct from nullif(btrim(input_title), '')
     or (review_row.payload ->> 'scheduled_date')::date is distinct from input_event_date
     or (review_row.payload ->> 'base_expectation_version')::integer is distinct from input_expected_base_version then
    raise exception 'assistant_proposal_reviewed_canonical_identity_conflict' using errcode = '55000';
  end if;

  proposal_event_at_text := review_row.payload ->> 'event_at';
  proposal_event_time_status := review_row.payload ->> 'event_time_status';
  if proposal_event_at_text is null or btrim(proposal_event_at_text) = '' then
    raise exception 'assistant_proposal_event_at_missing' using errcode = '22023';
  end if;
  -- PostgreSQL otherwise accepts a timezone-less timestamp by guessing the
  -- session timezone. Require the reviewed text itself to carry an explicit zone.
  if proposal_event_at_text !~ '(Z|[+-][0-9]{2}(?::?[0-9]{2})?)$' then
    raise exception 'assistant_proposal_event_at_timezone_required' using errcode = '22023';
  end if;

  begin
    proposal_event_at := proposal_event_at_text::timestamptz;
    proposal_local_event_at := regexp_replace(
      proposal_event_at_text,
      '(Z|[+-][0-9]{2}(?::?[0-9]{2})?)$',
      ''
    )::timestamp;
  exception when others then
    raise exception 'assistant_proposal_event_at_invalid' using errcode = '22023';
  end;

  if proposal_event_at is distinct from input_event_at
     or proposal_event_time_status is distinct from input_event_time_status then
    raise exception 'assistant_proposal_event_time_identity_conflict' using errcode = '55000';
  end if;
  if proposal_event_at <= clock_timestamp() then
    raise exception 'assistant_proposal_event_at_not_future' using errcode = '55000';
  end if;
  if proposal_event_time_status in ('estimated', 'unknown')
     and proposal_local_event_at::time = time '00:00:00' then
    raise exception 'assistant_proposal_ambiguous_midnight_event_at' using errcode = '55000';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('manual' || chr(31) || input_external_key, 3)
  );

  select count(*) into existing_event_count
  from public.tracked_market_events
  where source = 'manual' and external_key = input_external_key;
  if existing_event_count > 1 then
    raise exception 'assistant_proposal_multiple_canonical_events' using errcode = '55000';
  end if;

  if existing_event_count = 1 then
    select * into existing_event
    from public.tracked_market_events
    where source = 'manual' and external_key = input_external_key
    for update;

    -- A pre-existing ordinary manual event with the same key is not assistant
    -- provenance. Refuse to adopt it unless this proposal already owns it.
    select * into bound_row
    from public.assistant_proposal_materializations
    where tracked_event_id = existing_event.id;
    if found and bound_row.proposal_id is distinct from input_proposal_id then
      raise exception 'assistant_proposal_tracked_event_owned_by_different_proposal' using errcode = '55000';
    elsif not found and existing_event.created_by is distinct from input_actor then
      raise exception 'assistant_proposal_manual_identity_collision' using errcode = '55000';
    end if;

    perform pg_advisory_xact_lock(hashtextextended('tracked:' || existing_event.id::text, 1));
    select version into current_version
    from public.event_expectation_versions
    where event_id = 'tracked:' || existing_event.id::text
    order by version desc limit 1;
    if current_version is not null and current_version <> input_expected_base_version then
      raise exception 'expectation_version_conflict: expected % but current is %',
        input_expected_base_version, current_version using errcode = 'P0001';
    end if;
  end if;

  if proposal_event_at <= clock_timestamp() then
    raise exception 'assistant_proposal_event_at_not_future' using errcode = '55000';
  end if;

  select * into canonical_write
  from public.upsert_canonical_tracked_market_event(
    input_company_name, input_instrument, input_market, 'manual', input_external_key,
    input_kind, input_title, input_event_at, input_event_date, input_event_time_status,
    input_actor, null, input_expected_tracked_instrument_id
  );

  if canonical_write.out_id is null then
    raise exception 'assistant_proposal_canonical_write_missing' using errcode = '55000';
  end if;

  insert into public.assistant_proposal_materializations (
    proposal_id, review_round, tracked_event_id, event_id, updated_at
  ) values (
    input_proposal_id, input_expected_review_round, canonical_write.out_id,
    'tracked:' || canonical_write.out_id::text, clock_timestamp()
  )
  on conflict (proposal_id) do update
  set review_round = excluded.review_round,
      updated_at = excluded.updated_at
  where public.assistant_proposal_materializations.tracked_event_id = excluded.tracked_event_id
    and public.assistant_proposal_materializations.event_id = excluded.event_id;

  select * into bound_row
  from public.assistant_proposal_materializations
  where proposal_id = input_proposal_id;
  if not found
     or bound_row.review_round is distinct from input_expected_review_round
     or bound_row.tracked_event_id is distinct from canonical_write.out_id
     or bound_row.event_id is distinct from ('tracked:' || canonical_write.out_id::text) then
    raise exception 'assistant_proposal_materialization_binding_conflict' using errcode = '55000';
  end if;

  return query select
    canonical_write.out_id, canonical_write.out_tracked_instrument_id,
    canonical_write.out_calendar_event_id, canonical_write.out_company_name,
    canonical_write.out_instrument, canonical_write.out_market,
    canonical_write.out_source, canonical_write.out_external_key,
    canonical_write.out_kind, canonical_write.out_title, canonical_write.out_event_at,
    canonical_write.out_event_date, canonical_write.out_event_time_status,
    canonical_write.out_status, canonical_write.out_reference_price,
    canonical_write.out_reference_captured_at, canonical_write.out_created_by,
    canonical_write.out_updated_by, canonical_write.out_created_at,
    canonical_write.out_updated_at, canonical_write.out_action;
end;
$$;

revoke all on function public.upsert_assistant_proposal_canonical_tracked_event(
  uuid, integer, integer, text, text, text, text, text, text, text, timestamptz, date, text, text, text
) from public, anon, authenticated;
grant execute on function public.upsert_assistant_proposal_canonical_tracked_event(
  uuid, integer, integer, text, text, text, text, text, text, text, text, timestamptz, date, text, text, text
) to service_role;

create or replace function public.approve_assistant_proposal_strategy_draft(
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
  review_row public.assistant_event_proposal_reviews%rowtype;
  binding public.assistant_proposal_materializations%rowtype;
  strategy jsonb;
  official_source jsonb;
  source_state record;
  written_source record;
  current_version integer;
  next_version integer;
  inserted_at timestamptz;
  expected_source_actor text;
begin
  select * into proposal_row
  from public.assistant_event_proposals where id = input_proposal_id for update;
  if not found or proposal_row.status <> 'approved_for_materialization' then
    raise exception 'assistant_proposal_not_approved_for_materialization' using errcode = '55000';
  end if;
  if proposal_row.review_round is distinct from input_expected_review_round then
    raise exception 'assistant_proposal_review_round_conflict' using errcode = '40001';
  end if;

  select * into review_row
  from public.assistant_event_proposal_reviews
  where proposal_id = input_proposal_id
    and review_round = input_expected_review_round
    and decision = 'approved_for_materialization';
  if not found then
    raise exception 'assistant_proposal_review_snapshot_missing' using errcode = '55000';
  end if;
  if review_row.payload is distinct from proposal_row.payload
     or review_row.reviewed_by is distinct from proposal_row.reviewed_by
     or review_row.requested_execution_mode is distinct from 'demo' then
    raise exception 'assistant_proposal_review_snapshot_conflict' using errcode = '55000';
  end if;

  select * into binding
  from public.assistant_proposal_materializations
  where proposal_id = input_proposal_id;
  if not found
     or binding.review_round is distinct from input_expected_review_round
     or binding.event_id is distinct from input_event_id then
    raise exception 'assistant_proposal_materialization_binding_conflict' using errcode = '55000';
  end if;

  strategy := review_row.payload -> 'strategy';
  official_source := review_row.payload -> 'official_source';
  if jsonb_typeof(strategy) is distinct from 'object'
     or jsonb_typeof(official_source) is distinct from 'object' then
    raise exception 'assistant_proposal_review_payload_invalid' using errcode = '55000';
  end if;

  -- Every persisted strategy field is derived from or compared with the exact
  -- immutable review snapshot. Text arrays use the same trim/empty/dedup
  -- semantics as normalize_draft() in Python.
  if (review_row.payload ->> 'base_expectation_version')::integer is distinct from input_expected_base_version
     or nullif(btrim(strategy ->> 'source_name'), '') is distinct from nullif(btrim(input_source_name), '')
     or nullif(btrim(strategy ->> 'source_url'), '') is distinct from nullif(btrim(input_source_url), '')
     or nullif(strategy ->> 'source_as_of', '')::date is distinct from input_source_as_of
     or coalesce(strategy -> 'consensus', '{}'::jsonb) is distinct from input_consensus
     or public.assistant_normalize_text_array(coalesce(strategy -> 'important_kpis', '[]'::jsonb)) is distinct from input_important_kpis
     or public.assistant_normalize_text_array(coalesce(strategy -> 'bull_case', '[]'::jsonb)) is distinct from input_bull_case
     or public.assistant_normalize_text_array(coalesce(strategy -> 'base_case', '[]'::jsonb)) is distinct from input_base_case
     or public.assistant_normalize_text_array(coalesce(strategy -> 'bear_case', '[]'::jsonb)) is distinct from input_bear_case
     or coalesce(strategy -> 'triggers', '{}'::jsonb) is distinct from input_triggers
     or public.assistant_normalize_text_array(coalesce(strategy -> 'invalidation_conditions', '[]'::jsonb)) is distinct from input_invalidation_conditions
     or btrim(strategy ->> 'change_note') is distinct from btrim(input_change_note) then
    raise exception 'assistant_proposal_reviewed_strategy_conflict' using errcode = '55000';
  end if;

  if official_source ->> 'source_kind' is distinct from input_official_source_kind
     or official_source ->> 'source_url' is distinct from input_official_source_url
     or official_source ->> 'source_title' is distinct from input_official_source_title then
    raise exception 'assistant_proposal_reviewed_official_source_conflict' using errcode = '55000';
  end if;

  if input_approved_via is distinct from ('assistant_proposal:' || input_proposal_id::text)
     or input_approved_by is distinct from review_row.reviewed_by then
    raise exception 'assistant_proposal_reviewer_or_via_conflict' using errcode = '55000';
  end if;
  expected_source_actor := 'assistant_proposal:' || input_proposal_id::text || ':reviewer:' || review_row.reviewed_by;
  if input_official_source_actor is distinct from expected_source_actor then
    raise exception 'assistant_proposal_source_actor_conflict' using errcode = '55000';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 1));
  select version into current_version
  from public.event_expectation_versions
  where event_id = input_event_id order by version desc limit 1;
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
      input_event_id, input_official_source_kind, input_official_source_url,
      input_official_source_title, input_official_source_expected_version,
      input_official_source_actor
    );
    if written_source.out_event_id is distinct from input_event_id
       or written_source.out_source_kind is distinct from input_official_source_kind
       or written_source.out_source_url is distinct from input_official_source_url
       or written_source.out_source_title is distinct from input_official_source_title then
      raise exception 'assistant_proposal_official_source_write_identity_conflict' using errcode = '55000';
    end if;
  else
    select * into source_state from public.get_audited_official_release_source_state(input_event_id);
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
    event_id, version, source_name, source_url, source_as_of, consensus,
    important_kpis, bull_case, base_case, bear_case, triggers,
    invalidation_conditions, change_note, created_at
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

create or replace function public.approve_paper_trading_task_for_event(
  input_tracked_event_id uuid,
  input_source_event_id text,
  input_instrument text,
  input_actor text,
  input_expected_expectation_version integer,
  input_max_position_value_usd numeric
)
returns public.trading_tasks
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  event_row public.tracked_market_events%rowtype;
  canonical_source_event_id text;
  actor text := btrim(input_actor);
  instrument_value text := upper(btrim(input_instrument));
  cap_value numeric := input_max_position_value_usd;
  current_version integer;
  active_task public.trading_tasks%rowtype;
  approved public.trading_tasks%rowtype;
  binding public.assistant_proposal_materializations%rowtype;
  proposal_row public.assistant_event_proposals%rowtype;
  receipt_count integer;
  receipt_event_id text;
  receipt_expectation_version integer;
begin
  if input_tracked_event_id is null then raise exception 'trading_task_invalid_tracked_event_id'; end if;
  if input_source_event_id is null or btrim(input_source_event_id) = '' then raise exception 'trading_task_invalid_source_event_id'; end if;
  if instrument_value = '' then raise exception 'trading_task_invalid_instrument'; end if;
  if actor is null or length(actor) not between 1 and 200 then raise exception 'trading_task_invalid_actor'; end if;
  if input_expected_expectation_version is null or input_expected_expectation_version < 1 then raise exception 'trading_task_invalid_expected_expectation_version'; end if;
  if cap_value is null or cap_value = 'NaN'::numeric or cap_value <= 0 then raise exception 'trading_task_invalid_position_cap'; end if;

  select * into event_row from public.tracked_market_events where id = input_tracked_event_id;
  if not found then raise exception 'trading_task_tracked_event_not_found'; end if;
  canonical_source_event_id := case
    when event_row.calendar_event_id is not null then 'calendar:' || event_row.calendar_event_id::text
    else 'tracked:' || event_row.id::text end;
  if input_source_event_id <> canonical_source_event_id then raise exception 'trading_task_event_identity_mismatch'; end if;
  if upper(btrim(event_row.instrument)) <> instrument_value then raise exception 'trading_task_instrument_mismatch'; end if;

  -- Durable assistant provenance: ordinary manual events cannot be confused by
  -- a coincidentally equal external key.
  select * into binding
  from public.assistant_proposal_materializations
  where tracked_event_id = input_tracked_event_id;

  if found then
    if binding.event_id is distinct from canonical_source_event_id then
      raise exception 'assistant_proposal_paper_authority_event_lineage_mismatch';
    end if;
    select * into proposal_row
    from public.assistant_event_proposals where id = binding.proposal_id;
    if not found or proposal_row.status <> 'materialized' then
      raise exception 'assistant_proposal_not_materialized_for_paper_authority';
    end if;
    if proposal_row.review_round is distinct from binding.review_round
       or proposal_row.requested_execution_mode <> 'demo' then
      raise exception 'assistant_proposal_paper_authority_review_lineage_mismatch';
    end if;

    select count(*) into receipt_count
    from public.event_strategy_approvals
    where approved_via = 'assistant_proposal:' || binding.proposal_id::text;
    if receipt_count <> 1 then
      raise exception 'assistant_proposal_paper_authority_receipt_missing_or_ambiguous';
    end if;
    select event_id, expectation_version into receipt_event_id, receipt_expectation_version
    from public.event_strategy_approvals
    where approved_via = 'assistant_proposal:' || binding.proposal_id::text;
    if receipt_event_id is distinct from binding.event_id
       or receipt_expectation_version is distinct from input_expected_expectation_version then
      raise exception 'assistant_proposal_paper_authority_receipt_lineage_mismatch';
    end if;
  end if;

  perform pg_advisory_xact_lock(hashtextextended(canonical_source_event_id, 1));
  perform pg_advisory_xact_lock(hashtextextended(canonical_source_event_id, 0));
  select version into current_version
  from public.current_event_expectations where event_id = canonical_source_event_id limit 1;
  if not found then raise exception 'trading_task_expectation_not_found'; end if;
  if current_version <> input_expected_expectation_version then raise exception 'trading_task_expectation_version_changed'; end if;
  if binding.proposal_id is not null and receipt_expectation_version is distinct from current_version then
    raise exception 'assistant_proposal_paper_authority_expectation_lineage_mismatch';
  end if;

  select * into active_task
  from public.trading_tasks
  where tracked_event_id = input_tracked_event_id and mode = 'PAPER' and state in ('pending', 'approved')
  limit 1 for update;
  if found
     and active_task.state = 'approved'
     and active_task.source_event_id = canonical_source_event_id
     and upper(btrim(active_task.instrument)) = instrument_value
     and active_task.approved_expectation_version = input_expected_expectation_version
     and active_task.max_position_value_usd is not distinct from cap_value then
    return active_task;
  end if;
  if found then perform public.cancel_trading_task(active_task.id, actor); end if;

  insert into public.trading_tasks (
    tracked_event_id, source_event_id, instrument, mode, state, created_by, created_at,
    approved_by, approved_at, approved_expectation_version, max_position_value_usd
  ) values (
    input_tracked_event_id, canonical_source_event_id, instrument_value, 'PAPER', 'approved',
    actor, now(), actor, now(), input_expected_expectation_version, cap_value
  ) returning * into approved;
  return approved;
end;
$$;

revoke all on function public.approve_paper_trading_task_for_event(uuid, text, text, text, integer, numeric)
  from public, anon, authenticated;
grant execute on function public.approve_paper_trading_task_for_event(uuid, text, text, text, integer, numeric)
  to service_role;

commit;
