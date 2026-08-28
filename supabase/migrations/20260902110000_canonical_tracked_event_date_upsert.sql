-- Producer-neutral tracked-event persistence boundary with an explicit event-local date.
--
-- Calendar, scanner, manual and future discovery producers must all be able to
-- persist the same canonical tracked-event identity without deriving the release
-- date from event_at UTC. Keep the already-reviewed upsert_tracked_market_event()
-- as the underlying identity/lifecycle writer for compatibility, and add one
-- atomic wrapper that persists event_date in the same database transaction.
begin;

create or replace function public.upsert_canonical_tracked_market_event(
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
as $$
declare
  upserted record;
  saved_row public.tracked_market_events%rowtype;
begin
  if input_event_date is null then
    raise exception 'tracked_market_event_date_required';
  end if;

  select * into upserted
  from public.upsert_tracked_market_event(
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
    input_calendar_event_id
  );

  if upserted.out_id is null then
    raise exception 'canonical_tracked_market_event_upsert_returned_no_event';
  end if;

  select * into saved_row
  from public.tracked_market_events
  where id = upserted.out_id
  for update;

  if saved_row.id is null then
    raise exception 'canonical_tracked_market_event_missing_after_upsert';
  end if;

  if saved_row.event_date is not null
     and saved_row.event_date is distinct from input_event_date then
    raise exception 'tracked_market_event_date_conflict';
  end if;

  if saved_row.event_date is null then
    -- event_date is event identity. Only fill a missing value before monitoring
    -- has begun or a reference has been captured. Exact retries remain safe.
    if saved_row.status <> 'tracked'
       or saved_row.reference_price is not null
       or saved_row.started_at is not null then
      raise exception 'tracked_market_event_date_locked';
    end if;

    update public.tracked_market_events
    set event_date = input_event_date,
        updated_by = input_actor,
        updated_at = now()
    where id = saved_row.id
    returning * into saved_row;
  end if;

  return query select
    saved_row.id,
    saved_row.tracked_instrument_id,
    saved_row.calendar_event_id,
    saved_row.company_name,
    saved_row.instrument,
    saved_row.market,
    saved_row.source,
    saved_row.external_key,
    saved_row.kind,
    saved_row.title,
    saved_row.event_at,
    saved_row.event_date,
    saved_row.event_time_status,
    saved_row.status,
    saved_row.reference_price,
    saved_row.reference_captured_at,
    saved_row.created_by,
    saved_row.updated_by,
    saved_row.created_at,
    saved_row.updated_at,
    upserted.out_action::text;
end;
$$;

revoke all on function public.upsert_canonical_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid
) from public;
grant execute on function public.upsert_canonical_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid
) to service_role;

commit;
