-- Close the lower-level tracked-event writer that still accepted arbitrary
-- calendar_event_id values. Calendar-owned registrations must go through
-- promote_calendar_event_to_tracked_runtime(); the generic tracked-event writer
-- remains available only for calendar-less persistence.
begin;

-- Serialize the cutover with all tracked-event writes so no pre-cutover caller
-- can commit a calendar binding outside the invariant scan below.
lock table public.tracked_market_events in share row exclusive mode;

-- Close the table API as an alternate identity writer while the same write
-- barrier is held. Runtime mutations continue through the reviewed RPCs; those
-- RPCs are SECURITY DEFINER where owner privileges are required. Removing direct
-- INSERT plus the calendar-binding identity/date columns prevents service-role
-- clients from bypassing the guarded writers after this cutover.
revoke insert on table public.tracked_market_events from service_role;
revoke update (
  calendar_event_id,
  instrument,
  source,
  external_key,
  kind,
  event_date
) on table public.tracked_market_events from service_role;

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

-- Calendar promotion is the one trusted runtime path that is allowed to create
-- a calendar binding. Keep its existing calendar-row validation and lock order,
-- but delegate the actual insert to the owner-only compatibility implementation
-- instead of the newly guarded generic runtime writer. SECURITY DEFINER is safe
-- here because all producer identity is re-read and validated from locked rows
-- before the private calendar-capable helper is called.
create or replace function public.promote_calendar_event_to_tracked_runtime(
  input_calendar_event_id uuid,
  input_expected_instrument text,
  input_expected_event_type text,
  input_expected_source text,
  input_expected_occurrence_key text,
  input_expected_scheduled_date date,
  input_event_at timestamptz,
  input_event_time_status text,
  input_actor text
)
returns table (
  out_event_id uuid,
  out_action text,
  out_calendar_status text
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  calendar_row public.calendar_events%rowtype;
  existing_runtime public.tracked_market_events%rowtype;
  promoted_event_id uuid;
  promoted_action text;
begin
  if input_event_at is null then
    raise exception 'calendar_runtime_event_at_required';
  end if;
  if input_event_time_status not in ('confirmed', 'estimated', 'unknown') then
    raise exception 'invalid_event_time_status';
  end if;
  if nullif(btrim(input_actor), '') is null then
    raise exception 'calendar_runtime_actor_required';
  end if;

  select * into calendar_row
  from public.calendar_events
  where id = input_calendar_event_id
  for update;

  if calendar_row.id is null then
    raise exception 'calendar_event_not_found' using errcode = 'P0002';
  end if;

  if calendar_row.status not in ('candidate', 'tracked') then
    raise exception 'calendar_event_not_promotable';
  end if;

  if calendar_row.instrument is distinct from input_expected_instrument
     or calendar_row.event_type is distinct from input_expected_event_type
     or calendar_row.source is distinct from input_expected_source
     or calendar_row.occurrence_key is distinct from input_expected_occurrence_key
     or calendar_row.scheduled_date is distinct from input_expected_scheduled_date then
    raise exception 'calendar_event_changed_before_promotion';
  end if;

  select * into existing_runtime
  from public.tracked_market_events
  where calendar_event_id = calendar_row.id
  for update;

  if existing_runtime.id is not null then
    if existing_runtime.instrument is distinct from upper(replace(calendar_row.instrument, ' ', ''))
       or existing_runtime.kind is distinct from calendar_row.event_type
       or existing_runtime.source is distinct from calendar_row.source
       or existing_runtime.external_key is distinct from ('calendar:' || calendar_row.id::text) then
      raise exception 'calendar_runtime_binding_identity_conflict';
    end if;

    if existing_runtime.event_date is not null
       and existing_runtime.event_date is distinct from calendar_row.scheduled_date then
      raise exception 'calendar_runtime_event_date_conflict';
    end if;

    if existing_runtime.event_date is null then
      update public.tracked_market_events
      set event_date = calendar_row.scheduled_date,
          updated_by = input_actor,
          updated_at = now()
      where id = existing_runtime.id;
    end if;

    perform * from public.ensure_calendar_release_shell(calendar_row.id);

    if calendar_row.status = 'candidate' then
      update public.calendar_events
      set status = 'tracked',
          updated_at = now()
      where id = calendar_row.id;
      calendar_row.status := 'tracked';
    end if;

    return query select existing_runtime.id, 'noop_existing'::text, calendar_row.status;
    return;
  end if;

  select u.out_id, u.out_action
    into promoted_event_id, promoted_action
  from public.upsert_tracked_market_event_calendar_compat_v11(
    calendar_row.company_name,
    calendar_row.instrument,
    calendar_row.market,
    calendar_row.source,
    'calendar:' || calendar_row.id::text,
    calendar_row.event_type,
    calendar_row.company_name || ' ' || calendar_row.event_type,
    input_event_at,
    input_event_time_status,
    input_actor,
    calendar_row.id
  ) as u;

  if promoted_event_id is null then
    raise exception 'calendar_runtime_promotion_upsert_returned_no_event';
  end if;

  update public.tracked_market_events
  set event_date = calendar_row.scheduled_date,
      updated_by = input_actor,
      updated_at = now()
  where id = promoted_event_id
    and event_date is null;

  select * into existing_runtime
  from public.tracked_market_events
  where id = promoted_event_id
  for update;

  if existing_runtime.event_date is distinct from calendar_row.scheduled_date then
    raise exception 'calendar_runtime_event_date_conflict';
  end if;

  perform * from public.ensure_calendar_release_shell(calendar_row.id);

  if calendar_row.status = 'candidate' then
    update public.calendar_events
    set status = 'tracked',
        updated_at = now()
    where id = calendar_row.id;
    calendar_row.status := 'tracked';
  end if;

  return query select promoted_event_id, promoted_action, calendar_row.status;
end;
$$;

revoke all on function public.promote_calendar_event_to_tracked_runtime(
  uuid, text, text, text, text, date, timestamptz, text, text
) from public, anon, authenticated;
grant execute on function public.promote_calendar_event_to_tracked_runtime(
  uuid, text, text, text, text, date, timestamptz, text, text
) to service_role;

-- Revalidate every persisted calendar binding while the write barrier is still
-- held. Include null-dated legacy bindings: once the compatibility writer is
-- closed, promotion is the only repair path and a malformed null-dated binding
-- would otherwise remain permanently stuck behind the identity guard.
do $$
declare
  conflict_report text;
begin
  select string_agg(
    format('%s[%s]', t.id, case
      when c.id is null then 'calendar_event_missing'
      when upper(replace(c.instrument, ' ', '')) is distinct from t.instrument then 'instrument_mismatch'
      when c.event_type is distinct from t.kind then 'kind_mismatch'
      when c.source is distinct from t.source then 'source_mismatch'
      when t.external_key is distinct from ('calendar:' || c.id::text) then 'external_key_mismatch'
      when t.event_date is null then 'event_date_missing'
      when c.scheduled_date is distinct from t.event_date then 'event_date_mismatch'
      else 'unknown_conflict'
    end), ', ' order by t.id)
  into conflict_report
  from public.tracked_market_events t
  left join public.calendar_events c on c.id = t.calendar_event_id
  where t.calendar_event_id is not null
    and (
      c.id is null
      or upper(replace(c.instrument, ' ', '')) is distinct from t.instrument
      or c.event_type is distinct from t.kind
      or c.source is distinct from t.source
      or t.external_key is distinct from ('calendar:' || c.id::text)
      or t.event_date is null
      or c.scheduled_date is distinct from t.event_date
    );

  if conflict_report is not null then
    raise exception 'tracked_calendar_binding_invariant_conflicts: %', conflict_report;
  end if;
end;
$$;

commit;
