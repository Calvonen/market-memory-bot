-- Allow provider corrections to a watchlist row after candidate->tracked,
-- but only until that calendar occurrence has entered the persistent
-- tracked-event runtime. Once a tracked_market_events row references the
-- calendar event, its date/identity-facing metadata are frozen here so a
-- later provider correction cannot silently drift the runtime lifecycle.
begin;

create or replace function public.upsert_calendar_candidate(
  input_company_name text,
  input_instrument text,
  input_market text,
  input_event_type text,
  input_occurrence_key text,
  input_scheduled_date date,
  input_source text
)
returns table (
  out_id uuid,
  out_company_name text,
  out_instrument text,
  out_market text,
  out_event_type text,
  out_occurrence_key text,
  out_scheduled_date date,
  out_source text,
  out_status text,
  out_created_at timestamptz,
  out_updated_at timestamptz,
  out_action text
)
language plpgsql as $$
declare
  existing_row public.calendar_events%rowtype;
  new_row public.calendar_events%rowtype;
begin
  insert into public.calendar_events (
    company_name, instrument, market, event_type, occurrence_key, scheduled_date, source, status
  ) values (
    input_company_name, input_instrument, input_market, input_event_type,
    input_occurrence_key, input_scheduled_date, input_source, 'candidate'
  )
  on conflict (instrument, event_type, source, occurrence_key) do nothing
  returning * into new_row;

  if new_row.id is not null then
    return query select
      new_row.id, new_row.company_name, new_row.instrument, new_row.market,
      new_row.event_type, new_row.occurrence_key, new_row.scheduled_date, new_row.source,
      new_row.status, new_row.created_at, new_row.updated_at, 'inserted'::text;
    return;
  end if;

  select * into existing_row
  from public.calendar_events
  where instrument = input_instrument
    and event_type = input_event_type
    and source = input_source
    and occurrence_key = input_occurrence_key
  for update;

  if existing_row.id is null then
    raise exception 'calendar_event_upsert_race_unresolved';
  end if;

  -- Candidate and TRACKED rows are provider-correctable only while the
  -- calendar occurrence has not entered persistent runtime. Binding wins
  -- independently of the current watchlist status: an already-bound row can
  -- be untracked back to candidate, but it must still remain frozen because
  -- the runtime lifecycle was created against these exact fields. Later
  -- lifecycle stages are always locked as before.
  if existing_row.status not in ('candidate', 'tracked')
     or exists (
       select 1
       from public.tracked_market_events t
       where t.calendar_event_id = existing_row.id
     ) then
    return query select
      existing_row.id, existing_row.company_name, existing_row.instrument, existing_row.market,
      existing_row.event_type, existing_row.occurrence_key, existing_row.scheduled_date,
      existing_row.source, existing_row.status,
      existing_row.created_at, existing_row.updated_at, 'skipped_locked'::text;
    return;
  end if;

  update public.calendar_events
  set company_name = case
        when input_company_name = input_instrument
             and existing_row.company_name <> existing_row.instrument
          then existing_row.company_name
        else input_company_name
      end,
      market = case
        when input_market = 'Unknown' and existing_row.market <> 'Unknown'
          then existing_row.market
        else input_market
      end,
      scheduled_date = input_scheduled_date,
      updated_at = now()
  where id = existing_row.id
  returning * into new_row;

  return query select
    new_row.id, new_row.company_name, new_row.instrument, new_row.market,
    new_row.event_type, new_row.occurrence_key, new_row.scheduled_date, new_row.source,
    new_row.status, new_row.created_at, new_row.updated_at, 'updated'::text;
end;
$$;

revoke all on function public.upsert_calendar_candidate from public;
grant execute on function public.upsert_calendar_candidate to service_role;

create or replace function public.calendar_candidate_upsert_version()
returns integer
language sql
immutable
security invoker
set search_path = public
as $$
  select 3;
$$;

revoke all on function public.calendar_candidate_upsert_version() from public;
grant execute on function public.calendar_candidate_upsert_version() to service_role;

drop function if exists public.verify_strategy_draft_schema();

create function public.verify_strategy_draft_schema()
returns table (
  event_strategy_approvals_table_exists boolean,
  approve_strategy_draft_function_exists boolean,
  insert_next_expectation_version_function_exists boolean,
  schema_version_matches boolean,
  calendar_events_table_exists boolean,
  upsert_calendar_candidate_function_exists boolean,
  transition_calendar_event_status_function_exists boolean,
  calendar_candidate_upsert_version_matches boolean,
  calendar_candidate_upsert_implementation_version integer
)
language sql
security invoker
set search_path = public
as $$
  select
    to_regclass('public.event_strategy_approvals') is not null,
    to_regprocedure(
      'public.approve_strategy_draft(text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text)'
    ) is not null,
    to_regprocedure(
      'public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)'
    ) is not null,
    public.strategy_draft_schema_version() = 1,
    to_regclass('public.calendar_events') is not null,
    to_regprocedure(
      'public.upsert_calendar_candidate(text, text, text, text, text, date, text)'
    ) is not null,
    to_regprocedure(
      'public.transition_calendar_event_status(uuid, text, text)'
    ) is not null,
    public.calendar_candidate_upsert_version() = 3,
    public.calendar_candidate_upsert_version();
$$;

revoke all on function public.verify_strategy_draft_schema() from public;
grant execute on function public.verify_strategy_draft_schema() to service_role;

commit;
