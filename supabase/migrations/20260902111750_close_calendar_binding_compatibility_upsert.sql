-- Close the producer-neutral canonical upsert compatibility path that still
-- accepted arbitrary calendar_event_id values. Calendar-owned registrations must
-- go through promote_calendar_event_to_tracked_runtime(), which validates the
-- calendar producer identity atomically. Producer-neutral canonical upserts are
-- calendar-less only.
begin;

-- Serialize this cutover with tracked-event writes. Transactions that started
-- before the migration must finish first; after the wrappers below are installed,
-- new producer-neutral calls cannot recreate a calendar binding mismatch.
lock table public.tracked_market_events in share row exclusive mode;

alter function public.upsert_canonical_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid
) rename to upsert_canonical_tracked_market_event_calendar_compat_v11;

alter function public.upsert_canonical_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid, text
) rename to upsert_canonical_tracked_market_event_bound_calendar_compat_v11;

-- The renamed implementations are callable only by their owner. Public/runtime
-- callers must use the guarded wrappers recreated under the canonical names.
revoke all on function public.upsert_canonical_tracked_market_event_calendar_compat_v11(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid
) from public, anon, authenticated, service_role;
revoke all on function public.upsert_canonical_tracked_market_event_bound_calendar_compat_v11(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid, text
) from public, anon, authenticated, service_role;

create function public.upsert_canonical_tracked_market_event(
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
security definer
set search_path = pg_catalog, public
as $$
begin
  if input_calendar_event_id is not null then
    raise exception 'canonical_tracked_market_event_calendar_binding_forbidden';
  end if;

  return query
  select *
  from public.upsert_canonical_tracked_market_event_calendar_compat_v11(
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
    null
  );
end;
$$;

create function public.upsert_canonical_tracked_market_event(
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
  input_calendar_event_id uuid,
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
security definer
set search_path = pg_catalog, public
as $$
begin
  if input_calendar_event_id is not null then
    raise exception 'canonical_tracked_market_event_calendar_binding_forbidden';
  end if;

  return query
  select *
  from public.upsert_canonical_tracked_market_event_bound_calendar_compat_v11(
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

revoke all on function public.upsert_canonical_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid
) from public, anon, authenticated;
grant execute on function public.upsert_canonical_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid
) to service_role;

revoke all on function public.upsert_canonical_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid, text
) from public, anon, authenticated;
grant execute on function public.upsert_canonical_tracked_market_event(
  text, text, text, text, text, text, text, timestamptz, date, text, text, uuid, text
) to service_role;

-- Revalidate the invariant while the write barrier is still held. If any
-- transaction that predated the cutover committed an incompatible binding, fail
-- before the v12 release-shell migration can advance the schema version.
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
