-- Fail closed for assistant-origin events in two places discovered by the first
-- end-to-end DEMO test:
--   1) do not materialize an assistant proposal with an already-reached event_at
--      or an estimated/unknown local-midnight date sentinel;
--   2) do not grant PAPER authority until the proposal has fully materialized and
--      its immutable strategy-approval receipt matches the exact canonical event
--      and expectation version being approved.
--
-- These guards live in the database write boundaries so every caller receives
-- the same protection. They do not add any LIVE path.

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
  input_event_at timestamp with time zone,
  input_event_date date,
  input_event_time_status text,
  input_actor text,
  input_expected_tracked_instrument_id text
)
returns table(
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
  out_event_at timestamp with time zone,
  out_event_date date,
  out_event_time_status text,
  out_status text,
  out_reference_price numeric,
  out_reference_captured_at timestamp with time zone,
  out_created_by text,
  out_updated_by text,
  out_created_at timestamp with time zone,
  out_updated_at timestamp with time zone,
  out_action text
)
language plpgsql
set search_path to 'pg_catalog', 'public'
as $$
declare
  proposal_row public.assistant_event_proposals%rowtype;
  existing_event public.tracked_market_events%rowtype;
  existing_event_count integer;
  current_version integer;
  proposal_event_at_text text;
  proposal_event_at timestamptz;
  proposal_local_event_at timestamp;
  proposal_event_time_status text;
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

  proposal_event_at_text := proposal_row.payload ->> 'event_at';
  proposal_event_time_status := proposal_row.payload ->> 'event_time_status';
  if proposal_event_at_text is null or btrim(proposal_event_at_text) = '' then
    raise exception 'assistant_proposal_event_at_missing' using errcode = '22023';
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

  if proposal_event_at is distinct from input_event_at then
    raise exception 'assistant_proposal_event_at_identity_conflict' using errcode = '55000';
  end if;
  if proposal_event_time_status is distinct from input_event_time_status then
    raise exception 'assistant_proposal_event_time_status_identity_conflict' using errcode = '55000';
  end if;

  if proposal_event_at <= clock_timestamp() then
    raise exception 'assistant_proposal_event_at_not_future' using errcode = '55000';
  end if;

  -- Inspect the local wall-clock component after removing the timezone suffix.
  -- This catches equivalent valid spellings such as +10:00, +1000, Z, and a
  -- space instead of T instead of relying on one exact ISO rendering.
  if proposal_event_time_status in ('estimated', 'unknown')
     and proposal_local_event_at::time = time '00:00:00' then
    raise exception 'assistant_proposal_ambiguous_midnight_event_at' using errcode = '55000';
  end if;

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

  -- The locks above can block long enough for a near-term event to begin.
  -- Recheck immediately before the canonical write while those locks are held.
  if proposal_event_at <= clock_timestamp() then
    raise exception 'assistant_proposal_event_at_not_future' using errcode = '55000';
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
  proposal_row public.assistant_event_proposals%rowtype;
  proposal_count integer;
  receipt_count integer;
  receipt_event_id text;
  receipt_expectation_version integer;
begin
  if input_tracked_event_id is null then
    raise exception 'trading_task_invalid_tracked_event_id';
  end if;
  if input_source_event_id is null or btrim(input_source_event_id) = '' then
    raise exception 'trading_task_invalid_source_event_id';
  end if;
  if instrument_value = '' then
    raise exception 'trading_task_invalid_instrument';
  end if;
  if actor is null or length(actor) not between 1 and 200 then
    raise exception 'trading_task_invalid_actor';
  end if;
  if input_expected_expectation_version is null or input_expected_expectation_version < 1 then
    raise exception 'trading_task_invalid_expected_expectation_version';
  end if;
  if cap_value is null or cap_value = 'NaN'::numeric or cap_value <= 0 then
    raise exception 'trading_task_invalid_position_cap';
  end if;

  select * into event_row
  from public.tracked_market_events
  where id = input_tracked_event_id;
  if not found then
    raise exception 'trading_task_tracked_event_not_found';
  end if;

  canonical_source_event_id := case
    when event_row.calendar_event_id is not null then 'calendar:' || event_row.calendar_event_id::text
    else 'tracked:' || event_row.id::text
  end;

  if input_source_event_id <> canonical_source_event_id then
    raise exception 'trading_task_event_identity_mismatch';
  end if;
  if upper(btrim(event_row.instrument)) <> instrument_value then
    raise exception 'trading_task_instrument_mismatch';
  end if;

  -- Assistant proposals materialize tracked events through the dedicated ingress
  -- with source='manual' and external_key=proposal_key. Match both halves of the
  -- canonical unique identity so an unrelated source reusing the same key keeps
  -- the ordinary PAPER behavior.
  select count(*) into proposal_count
  from public.assistant_event_proposals
  where proposal_key = event_row.external_key
    and event_row.source = 'manual';

  if proposal_count > 1 then
    raise exception 'assistant_proposal_paper_authority_ambiguous_lineage';
  elsif proposal_count = 1 then
    select * into proposal_row
    from public.assistant_event_proposals
    where proposal_key = event_row.external_key
      and event_row.source = 'manual';

    if proposal_row.requested_execution_mode <> 'demo' then
      raise exception 'assistant_proposal_live_locked';
    end if;
    if proposal_row.status <> 'materialized' then
      raise exception 'assistant_proposal_not_materialized_for_paper_authority';
    end if;

    select count(*) into receipt_count
    from public.event_strategy_approvals
    where approved_via = 'assistant_proposal:' || proposal_row.id::text;

    if receipt_count <> 1 then
      raise exception 'assistant_proposal_paper_authority_receipt_missing_or_ambiguous';
    end if;

    select event_id, expectation_version
      into receipt_event_id, receipt_expectation_version
    from public.event_strategy_approvals
    where approved_via = 'assistant_proposal:' || proposal_row.id::text;

    if receipt_event_id is distinct from canonical_source_event_id then
      raise exception 'assistant_proposal_paper_authority_event_lineage_mismatch';
    end if;
    if receipt_expectation_version is distinct from input_expected_expectation_version then
      raise exception 'assistant_proposal_paper_authority_expectation_lineage_mismatch';
    end if;
  end if;

  perform pg_advisory_xact_lock(hashtextextended(canonical_source_event_id, 1));
  perform pg_advisory_xact_lock(hashtextextended(canonical_source_event_id, 0));

  select version into current_version
  from public.current_event_expectations
  where event_id = canonical_source_event_id
  limit 1;
  if not found then
    raise exception 'trading_task_expectation_not_found';
  end if;
  if current_version <> input_expected_expectation_version then
    raise exception 'trading_task_expectation_version_changed';
  end if;

  if proposal_count = 1 and receipt_expectation_version is distinct from current_version then
    raise exception 'assistant_proposal_paper_authority_expectation_lineage_mismatch';
  end if;

  select * into active_task
  from public.trading_tasks
  where tracked_event_id = input_tracked_event_id
    and mode = 'PAPER'
    and state in ('pending', 'approved')
  limit 1
  for update;

  if found
     and active_task.state = 'approved'
     and active_task.source_event_id = canonical_source_event_id
     and upper(btrim(active_task.instrument)) = instrument_value
     and active_task.approved_expectation_version = input_expected_expectation_version
     and active_task.max_position_value_usd is not distinct from cap_value then
    return active_task;
  end if;

  if found then
    perform public.cancel_trading_task(active_task.id, actor);
  end if;

  insert into public.trading_tasks (
    tracked_event_id,
    source_event_id,
    instrument,
    mode,
    state,
    created_by,
    created_at,
    approved_by,
    approved_at,
    approved_expectation_version,
    max_position_value_usd
  ) values (
    input_tracked_event_id,
    canonical_source_event_id,
    instrument_value,
    'PAPER',
    'approved',
    actor,
    now(),
    actor,
    now(),
    input_expected_expectation_version,
    cap_value
  )
  returning * into approved;

  return approved;
end;
$$;

revoke all on function public.approve_paper_trading_task_for_event(uuid, text, text, text, integer, numeric)
  from public, anon, authenticated;
grant execute on function public.approve_paper_trading_task_for_event(uuid, text, text, text, integer, numeric)
  to service_role;
