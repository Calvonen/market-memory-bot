-- Repair one legacy class of producer-identity drift before the canonical
-- release-shell backfill runs. Historical tracked events could retain a
-- calendar_event_id even when their canonical source/external_key already
-- belonged to a non-calendar producer. Repair only unambiguous terminal rows,
-- serialize with the runtime lock order, and retain a durable audit link for
-- every migration-only quarantine/detach transition.
begin;

create table if not exists public.legacy_tracked_calendar_binding_repairs (
  id bigint generated always as identity primary key,
  calendar_event_id uuid not null,
  tracked_event_id uuid not null,
  prior_calendar_status text not null,
  prior_calendar_source text not null,
  tracked_source text not null,
  tracked_external_key text not null,
  repair_action text not null check (repair_action = 'quarantine_and_detach'),
  repair_reason text not null,
  repaired_at timestamptz not null default now(),
  unique (calendar_event_id, tracked_event_id, repair_action)
);

comment on table public.legacy_tracked_calendar_binding_repairs is
  'Audit trail for migration-only repairs that quarantine a stale calendar producer binding before detaching it from the canonical tracked event.';

-- This table is an operator-only immutable audit surface. Keep it out of the
-- public Data API even on projects whose public schema still has broad default
-- privileges. The migration itself runs as the database owner; runtime service
-- code only needs read access for diagnostics. Revoke service_role as well
-- before granting back SELECT so inherited/default write grants cannot survive.
alter table public.legacy_tracked_calendar_binding_repairs enable row level security;
revoke all on table public.legacy_tracked_calendar_binding_repairs from public, anon, authenticated, service_role;
grant select on table public.legacy_tracked_calendar_binding_repairs to service_role;
revoke all on sequence public.legacy_tracked_calendar_binding_repairs_id_seq from public, anon, authenticated, service_role;

do $$
declare
  candidate record;
  calendar_row public.calendar_events%rowtype;
  tracked_row public.tracked_market_events%rowtype;
  old_release_event_id text;
  conflict_report text;
begin
  for candidate in
    select t.id, t.calendar_event_id
    from public.tracked_market_events t
    join public.calendar_events c on c.id = t.calendar_event_id
    where t.kind = 'earnings'
      and t.event_date is not null
      and t.status in ('completed', 'failed', 'cancelled')
      and upper(replace(c.instrument, ' ', '')) = t.instrument
      and c.event_type = t.kind
      and c.scheduled_date = t.event_date
      and c.source is distinct from t.source
      -- Only detach identities that are clearly non-calendar. Historical keys
      -- with case differences or any leading/trailing whitespace (spaces, tabs,
      -- newlines, etc.) are malformed calendar identities, not safe evidence of
      -- a different producer, so leave them bound for the conflict report below.
      and lower(
        regexp_replace(t.external_key, '^[[:space:]]+|[[:space:]]+$', '', 'g')
      ) not like 'calendar:%'
    order by t.id
  loop
    -- Match the runtime release-shell lock protocol: calendar first, then tracked.
    -- If a worker is already creating release state, this waits for it to commit;
    -- the dependency checks below then run in fresh READ COMMITTED statements.
    select * into calendar_row
    from public.calendar_events
    where id = candidate.calendar_event_id
    for update;

    if calendar_row.id is null then
      continue;
    end if;

    select * into tracked_row
    from public.tracked_market_events
    where id = candidate.id
    for update;

    if tracked_row.id is null
       or tracked_row.calendar_event_id is distinct from candidate.calendar_event_id then
      continue;
    end if;

    -- Recheck the complete safe-detach predicate after both locks are held.
    if tracked_row.kind <> 'earnings'
       or tracked_row.event_date is null
       or tracked_row.status not in ('completed', 'failed', 'cancelled')
       or upper(replace(calendar_row.instrument, ' ', '')) is distinct from tracked_row.instrument
       or calendar_row.event_type is distinct from tracked_row.kind
       or calendar_row.scheduled_date is distinct from tracked_row.event_date
       or calendar_row.source is not distinct from tracked_row.source
       or lower(
         regexp_replace(tracked_row.external_key, '^[[:space:]]+|[[:space:]]+$', '', 'g')
       ) like 'calendar:%' then
      continue;
    end if;

    old_release_event_id := 'calendar:' || calendar_row.id::text;

    -- Do not change release identity when any state already exists under the old
    -- calendar identity. These checks intentionally happen after the row locks.
    if exists (select 1 from public.market_events where event_id = old_release_event_id)
       or exists (select 1 from public.event_expectation_versions where event_id = old_release_event_id)
       or exists (select 1 from public.event_official_release_sources where event_id = old_release_event_id)
       or exists (select 1 from public.event_official_release_source_audit where event_id = old_release_event_id)
       or exists (select 1 from public.event_source_documents where event_id = old_release_event_id)
       or exists (select 1 from public.event_ai_analyses where event_id = old_release_event_id)
       or exists (select 1 from public.event_ingestion_runs where event_id = old_release_event_id)
       or exists (select 1 from public.event_strategy_approvals where event_id = old_release_event_id)
       or exists (select 1 from public.event_paper_trade_event_claims where event_id = old_release_event_id)
       or exists (select 1 from public.event_paper_trade_runs where event_id = old_release_event_id) then
      continue;
    end if;

    -- This is an exceptional migration-only lifecycle repair, not a normal
    -- calendar control-surface transition. Preserve the original visible state,
    -- producer identity and tracked-event link durably before changing either row.
    insert into public.legacy_tracked_calendar_binding_repairs (
      calendar_event_id,
      tracked_event_id,
      prior_calendar_status,
      prior_calendar_source,
      tracked_source,
      tracked_external_key,
      repair_action,
      repair_reason
    ) values (
      calendar_row.id,
      tracked_row.id,
      calendar_row.status,
      calendar_row.source,
      tracked_row.source,
      tracked_row.external_key,
      'quarantine_and_detach',
      'legacy calendar binding conflicts with canonical non-calendar producer identity'
    )
    on conflict (calendar_event_id, tracked_event_id, repair_action) do nothing;

    -- A detached calendar row must not remain trackable. Otherwise an idempotent
    -- retry would see no bound runtime and promote the same occurrence again.
    -- `research` is an existing non-trackable, sync-locked lifecycle state; the
    -- audit row above records the prior state and durable detached-runtime link.
    if calendar_row.status in ('candidate', 'tracked') then
      update public.calendar_events
      set status = 'research',
          updated_at = now()
      where id = calendar_row.id
        and status = calendar_row.status;
    end if;

    update public.tracked_market_events
    set calendar_event_id = null,
        updated_by = 'migration:repair-legacy-tracked-calendar-binding',
        updated_at = now()
    where id = tracked_row.id
      and calendar_event_id = calendar_row.id;
  end loop;

  -- Any mismatch that remains after the serialized repair is actionable and must
  -- abort instead of being silently skipped by the v12 backfill.
  select string_agg(
    format(
      '%s[%s]',
      t.id,
      case
        when c.id is null then 'calendar_event_missing'
        when upper(replace(c.instrument, ' ', '')) is distinct from t.instrument
          then 'instrument_mismatch'
        when c.event_type is distinct from t.kind then 'kind_mismatch'
        when c.scheduled_date is distinct from t.event_date then 'event_date_mismatch'
        when c.source is distinct from t.source then 'source_mismatch'
        when t.external_key is distinct from ('calendar:' || c.id::text)
          then 'external_key_mismatch'
        else 'unknown_conflict'
      end
    ),
    ', ' order by t.id
  ) into conflict_report
  from public.tracked_market_events t
  left join public.calendar_events c on c.id = t.calendar_event_id
  where t.kind = 'earnings'
    and t.event_date is not null
    and t.calendar_event_id is not null
    and (
      c.id is null
      or upper(replace(c.instrument, ' ', '')) is distinct from t.instrument
      or c.event_type is distinct from t.kind
      or c.scheduled_date is distinct from t.event_date
      or c.source is distinct from t.source
      or t.external_key is distinct from ('calendar:' || c.id::text)
    );

  if conflict_report is not null then
    raise exception 'tracked_release_shell_legacy_binding_conflicts: %', conflict_report;
  end if;
end;
$$;

commit;
