-- Give calendar-less market-open events a canonical source-event/expectation identity
-- before the session opens, then freeze the first confirmed opening-pattern evidence
-- exactly once under that expectation version. This reuses the existing PAPER task,
-- lease and broker-attempt authority without pretending a market-open is an earnings
-- release or an official company document.
begin;

create or replace function public.ensure_market_open_strategy_shell(
  input_tracked_event_id uuid
)
returns table (
  out_event_id text,
  out_expectation_version integer,
  out_action text
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  tracked_row public.tracked_market_events%rowtype;
  source_event_id text;
  existing_market_event public.market_events%rowtype;
  current_version integer;
  action_value text := 'noop_existing';
begin
  if input_tracked_event_id is null then
    raise exception 'market_open_invalid_tracked_event_id';
  end if;

  select * into tracked_row
  from public.tracked_market_events
  where id = input_tracked_event_id
  for update;
  if not found then
    raise exception 'market_open_tracked_event_not_found';
  end if;
  if tracked_row.kind <> 'market_open' then
    raise exception 'tracked_event_not_market_open';
  end if;
  if tracked_row.calendar_event_id is not null then
    raise exception 'market_open_calendar_binding_not_supported';
  end if;
  if tracked_row.event_date is null then
    raise exception 'market_open_event_date_required';
  end if;

  source_event_id := 'tracked:' || tracked_row.id::text;
  perform pg_advisory_xact_lock(hashtextextended(source_event_id, 1));

  select * into existing_market_event
  from public.market_events
  where event_id = source_event_id
  for update;

  if existing_market_event.event_id is null then
    insert into public.market_events (
      event_id, instrument, event_name, scheduled_date, status
    ) values (
      source_event_id,
      tracked_row.instrument,
      tracked_row.instrument || ' market open',
      tracked_row.event_date,
      'scheduled'
    );
    action_value := 'inserted_market_event';
  elsif upper(btrim(existing_market_event.instrument)) <> upper(btrim(tracked_row.instrument))
     or existing_market_event.scheduled_date <> tracked_row.event_date then
    raise exception 'market_open_strategy_shell_identity_conflict';
  end if;

  select version into current_version
  from public.current_event_expectations
  where event_id = source_event_id
  limit 1;

  if current_version is null then
    insert into public.event_expectation_versions (
      event_id,
      version,
      source_name,
      source_url,
      source_as_of,
      consensus,
      important_kpis,
      bull_case,
      base_case,
      bear_case,
      triggers,
      invalidation_conditions,
      change_note
    ) values (
      source_event_id,
      1,
      'tracked:market_open:strategy-shell',
      null,
      tracked_row.event_date,
      '{}'::jsonb,
      '[]'::jsonb,
      array[
        'LONG only after completed 1m opening weakness reverses positive and a later completed 1m candle confirms the reclaim.'
      ]::text[],
      array[
        'NO TRADE until a reviewed market-open pattern plus aligned Technical and Market Memory evidence is present.'
      ]::text[],
      array[
        'SHORT only after completed opening weakness, a bounce attempt, and a later completed 1m candle rolls back negative.'
      ]::text[],
      jsonb_build_object(
        'event_kind', 'market_open',
        'opening_window_minutes', 30,
        'setup_score', 35,
        'confirmation_score', 25,
        'strategy_threshold_unchanged', true,
        'technical_same_direction_required', true,
        'market_memory_same_direction_required', true
      ),
      array[
        'Opening pattern no longer matches the frozen complete 1m reaction evidence.',
        'Technical or Market Memory does not confirm the same direction.',
        'Risk Engine or broker guard rejects execution.'
      ]::text[],
      'automatic canonical market-open strategy shell; no earnings consensus or release evidence inferred'
    );
    current_version := 1;
    action_value := case
      when action_value = 'noop_existing' then 'inserted_expectation'
      else 'inserted'
    end;
  end if;

  return query select source_event_id, current_version, action_value;
end;
$$;

revoke all on function public.ensure_market_open_strategy_shell(uuid)
  from public, anon, authenticated;
grant execute on function public.ensure_market_open_strategy_shell(uuid) to service_role;

create or replace function public.ensure_calendarless_market_open_strategy_shell_after_date_write()
returns trigger
language plpgsql
security invoker
as $$
begin
  perform * from public.ensure_market_open_strategy_shell(new.id);
  return new;
end;
$$;

revoke all on function public.ensure_calendarless_market_open_strategy_shell_after_date_write()
  from public;

drop trigger if exists tracked_market_events_market_open_shell_after_date_write
  on public.tracked_market_events;
create trigger tracked_market_events_market_open_shell_after_date_write
  after insert or update of event_date on public.tracked_market_events
  for each row
  when (
    new.kind = 'market_open'
    and new.event_date is not null
    and new.calendar_event_id is null
  )
  execute function public.ensure_calendarless_market_open_strategy_shell_after_date_write();

create or replace function public.freeze_market_open_evidence(
  input_tracked_event_id uuid,
  input_expectation_version integer,
  input_raw_text text,
  input_analysis jsonb
)
returns table (
  out_analysis_id uuid,
  out_source_document_id uuid,
  out_created boolean,
  out_raw_text text,
  out_analysis jsonb
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  tracked_row public.tracked_market_events%rowtype;
  source_event_id text;
  current_version integer;
  existing_count integer;
  analysis_row public.event_ai_analyses%rowtype;
  document_row public.event_source_documents%rowtype;
  content_hash text;
begin
  if input_tracked_event_id is null
     or input_expectation_version is null
     or input_expectation_version < 1 then
    raise exception 'market_open_evidence_identity_invalid';
  end if;

  select * into tracked_row
  from public.tracked_market_events
  where id = input_tracked_event_id;
  if not found then
    raise exception 'market_open_tracked_event_not_found';
  end if;
  if tracked_row.kind <> 'market_open'
     or tracked_row.calendar_event_id is not null then
    raise exception 'market_open_evidence_event_invalid';
  end if;

  source_event_id := 'tracked:' || tracked_row.id::text;
  perform pg_advisory_xact_lock(hashtextextended(source_event_id, 1));
  perform pg_advisory_xact_lock(hashtextextended(source_event_id, 0));

  select version into current_version
  from public.current_event_expectations
  where event_id = source_event_id
  limit 1;
  if not found or current_version <> input_expectation_version then
    raise exception 'market_open_evidence_expectation_changed';
  end if;

  select count(*)::integer into existing_count
  from public.event_ai_analyses
  where event_id = source_event_id
    and expectation_version = input_expectation_version;
  if existing_count > 1 then
    raise exception 'market_open_evidence_ambiguous';
  end if;

  if existing_count = 1 then
    select * into analysis_row
    from public.event_ai_analyses
    where event_id = source_event_id
      and expectation_version = input_expectation_version
    limit 1;
    if analysis_row.provider <> 'rule_engine'
       or analysis_row.model <> 'market-open-v1' then
      raise exception 'market_open_evidence_type_conflict';
    end if;
    select * into document_row
    from public.event_source_documents
    where id = analysis_row.source_document_id;
    if not found or document_row.source_type <> 'market_open_reaction_evidence' then
      raise exception 'market_open_evidence_document_conflict';
    end if;
    return query select analysis_row.id, document_row.id, false,
      document_row.raw_text, analysis_row.analysis;
    return;
  end if;

  if input_raw_text is null or btrim(input_raw_text) = ''
     or input_analysis is null
     or jsonb_typeof(input_analysis) <> 'object' then
    raise exception 'market_open_evidence_payload_invalid';
  end if;

  content_hash := encode(
    extensions.digest(convert_to(input_raw_text, 'UTF8'), 'sha256'),
    'hex'
  );

  insert into public.event_source_documents (
    event_id,
    source_type,
    source_url,
    source_title,
    content_sha256,
    raw_text
  ) values (
    source_event_id,
    'market_open_reaction_evidence',
    'marketai://tracked-events/' || tracked_row.id::text || '/market-open',
    tracked_row.instrument || ' frozen market-open reaction evidence',
    content_hash,
    input_raw_text
  )
  returning * into document_row;

  insert into public.event_ai_analyses (
    event_id,
    source_document_id,
    expectation_version,
    provider,
    model,
    analysis,
    raw_response
  ) values (
    source_event_id,
    document_row.id,
    input_expectation_version,
    'rule_engine',
    'market-open-v1',
    input_analysis,
    input_raw_text
  )
  returning * into analysis_row;

  return query select analysis_row.id, document_row.id, true,
    document_row.raw_text, analysis_row.analysis;
end;
$$;

revoke all on function public.freeze_market_open_evidence(uuid, integer, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.freeze_market_open_evidence(uuid, integer, text, jsonb)
  to service_role;

commit;
