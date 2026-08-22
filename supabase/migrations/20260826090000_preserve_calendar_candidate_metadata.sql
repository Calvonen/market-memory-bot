-- Preserve already-enriched calendar metadata when an optional provider
-- metadata lookup temporarily falls back to placeholders.
--
-- This is a follow-up migration: the original calendar migration may already
-- be applied in production, so do not edit it in place.

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

  if existing_row.status <> 'candidate' then
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
