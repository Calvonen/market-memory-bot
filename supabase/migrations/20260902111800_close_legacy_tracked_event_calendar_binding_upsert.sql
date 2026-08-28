-- Close the lower-level tracked-event writer that still accepted arbitrary
-- calendar_event_id values. Calendar-owned registrations must go through
-- promote_calendar_event_to_tracked_runtime(); the generic tracked-event writer
-- remains available only for calendar-less persistence.
begin;

-- Serialize the cutover with all tracked-event writes so no pre-cutover caller
-- can commit a calendar binding outside the invariant scan below.
lock table public.tracked_market_events in share row exclusive mode;

alter function public.upsert_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, text, text, uuid
) rename to upsert_tracked_market_event_calendar_compat_v11;

-- Preserve the reviewed implementation for owner-only delegation, but remove
-- every runtime/API execution path to the calendar-capable compatibility body.
revoke all on function public.upsert_tracked_market_event_calendar_compat_v11(
  text, text, text, text, text, text, text, timestamptz, text, text, uuid
) from public, anon, authenticated, service_role;

create function public.upsert_tracked_market_event(
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

-- Revalidate every persisted calendar binding while the write barrier is still
-- held. The next migration may only advance runtime schema v12 from a clean
-- state that cannot be recreated through either canonical or legacy RPCs.
do $$
declare
  conflict_report text;
begin
  select string_agg(
    format('%s[%s]', t.id, case
      when c.id is null then 'calendar_event_missing'
      when upper(replace(c.instrument, ' ', '')) is distinct from t.instrument then 'instrument_mismatch'
      when c.event_type is distinct from t.kind then 'kind_mismatch'
      when c.scheduled_date is distinct from t.event_date then 'event_date_mismatch'
      when c.source is distinct from t.source then 'source_mismatch'
      when t.external_key is distinct from ('calendar:' || c.id::text) then 'external_key_mismatch'
      else 'unknown_conflict'
    end), ', ' order by t.id)
  into conflict_report
  from public.tracked_market_events t
  left join public.calendar_events c on c.id = t.calendar_event_id
  where t.calendar_event_id is not null
    and t.event_date is not null
    and (
      c.id is null
      or upper(replace(c.instrument, ' ', '')) is distinct from t.instrument
      or c.event_type is distinct from t.kind
      or c.scheduled_date is distinct from t.event_date
      or c.source is distinct from t.source
      or t.external_key is distinct from ('calendar:' || c.id::text)
    );

  if conflict_report is not null then
    raise exception 'tracked_calendar_binding_invariant_conflicts: %', conflict_report;
  end if;
end;
$$;

commit;
