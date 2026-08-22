-- Calendar / watchlist storage boundary for the free earnings-calendar MVP.
--
-- Deliberately its own table, not event_expectation_versions or
-- market_events: those are versioned storage for events already promoted
-- into the trading system (see docs/event_configuration_storage.md). A
-- candidate or tracked calendar event has no expectation version and no
-- consensus - it must stay unreachable from EventExpectationRepository, the
-- release worker, and the PAPER pipeline for as long as it is only
-- candidate/tracked. A later phase, not this migration, is responsible for
-- turning an approved calendar event into an actual market_events row.

create table if not exists public.calendar_events (
  id uuid primary key default gen_random_uuid(),
  company_name text not null,
  instrument text not null,
  market text not null,
  -- Not constrained to 'earnings': a manually-entered event (e.g. a
  -- production report no calendar provider tracks) is exactly as valid a
  -- candidate as a provider-sourced earnings date. See
  -- trading_system/calendar_provider.py's CalendarCandidate.
  event_type text not null,
  -- Disambiguates *which* recurrence of (instrument, event_type, source)
  -- this row is - e.g. "2026Q3" vs "2026Q4" for two quarterly earnings
  -- releases of the same company. Deliberately not scheduled_date (see
  -- below) and deliberately not optional: without it, every future
  -- occurrence of the same instrument/event_type/source would collide into
  -- one row, silently losing the next quarter's release once the current
  -- one is tracked. See CalendarCandidate.occurrence_key.
  occurrence_key text not null,
  scheduled_date date not null,
  source text not null,
  status text not null default 'candidate' check (
    status in (
      'candidate',
      'tracked',
      'research',
      'decision_to_prepare_strategy',
      'enrich_event_details',
      'preview',
      'approve'
    )
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- Identity for idempotent provider sync deliberately excludes
  -- scheduled_date: a provider may move a still-candidate event's date on a
  -- later sync (see upsert_calendar_candidate() below), so the date can
  -- never be part of what identifies "the same event occurrence".
  unique (instrument, event_type, source, occurrence_key)
);

alter table public.calendar_events enable row level security;
revoke all on table public.calendar_events from anon, authenticated;
grant select, insert, update on table public.calendar_events to service_role;

create index if not exists calendar_events_scheduled_date_idx
  on public.calendar_events(scheduled_date);

-- Idempotent, race-safe upsert used by the provider sync worker (and by
-- manual event entry).
--
-- The first-insert path is a plain `insert ... on conflict (...) do
-- nothing`: this is what actually makes concurrent first-sight-of-a-
-- candidate calls race-safe. `select ... for update` cannot lock a row that
-- does not exist yet, so two concurrent callers racing to insert the *same*
-- brand-new occurrence would otherwise both pass a "not found" check and
-- both attempt to insert, and the loser would fail with a raw unique-
-- violation instead of getting back an idempotent result. `on conflict ...
-- do nothing` makes Postgres itself serialize the two inserts against the
-- same unique index entry: exactly one of them inserts the row, and the
-- other's insert becomes a no-op (`new_row.id` stays null) rather than
-- raising - it falls through to the same locked read+update-or-skip path
-- used for any other already-existing row, so both callers still observe a
-- consistent, idempotent outcome (one 'inserted', the other 'updated' or
-- 'skipped_locked' depending on what actually happened to the row by the
-- time it reads it).
--
-- Once a row exists, the `select ... for update` + conditional `update`
-- pair is what makes "already tracked/beyond -> never silently overwritten"
-- atomic against a concurrent sync or track()/untrack() call - not just
-- "usually true" from running syncs one at a time.
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

  -- Either this call lost the insert race above (a concurrent caller won),
  -- or the row already existed from an earlier sync - either way, lock it
  -- now and apply the same "candidate only" update-or-skip rule.
  select * into existing_row
  from public.calendar_events
  where instrument = input_instrument
    and event_type = input_event_type
    and source = input_source
    and occurrence_key = input_occurrence_key
  for update;

  if existing_row.id is null then
    -- Should be unreachable (the insert above only no-ops on a genuine
    -- conflict), but fail loudly rather than return an empty result set if
    -- it ever happens.
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
  set company_name = input_company_name,
      market = input_market,
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

-- Race-safe candidate<->tracked transition. input_from_status/input_to_status
-- are fixed by the caller (track(): candidate->tracked; untrack():
-- tracked->candidate) - never caller-supplied arbitrary states, so this
-- function can never be used to jump a row into research/preview/approve
-- etc. from the API layer this migration ships with.
create or replace function public.transition_calendar_event_status(
  input_calendar_event_id uuid,
  input_from_status text,
  input_to_status text
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
  select * into existing_row
  from public.calendar_events
  where id = input_calendar_event_id
  for update;

  if existing_row.id is null then
    raise exception 'calendar_event_not_found' using errcode = 'P0002';
  end if;

  if existing_row.status = input_to_status then
    return query select
      existing_row.id, existing_row.company_name, existing_row.instrument, existing_row.market,
      existing_row.event_type, existing_row.occurrence_key, existing_row.scheduled_date,
      existing_row.source, existing_row.status,
      existing_row.created_at, existing_row.updated_at, 'noop_already'::text;
    return;
  end if;

  if existing_row.status <> input_from_status then
    raise exception 'invalid_calendar_event_transition' using errcode = 'P0001';
  end if;

  update public.calendar_events
  set status = input_to_status,
      updated_at = now()
  where id = existing_row.id
  returning * into new_row;

  return query select
    new_row.id, new_row.company_name, new_row.instrument, new_row.market,
    new_row.event_type, new_row.occurrence_key, new_row.scheduled_date, new_row.source,
    new_row.status, new_row.created_at, new_row.updated_at, 'updated'::text;
end;
$$;

revoke all on function public.transition_calendar_event_status from public;
grant execute on function public.transition_calendar_event_status to service_role;
