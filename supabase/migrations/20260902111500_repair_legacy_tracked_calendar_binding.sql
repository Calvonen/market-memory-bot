-- Repair one legacy class of producer-identity drift before the canonical
-- release-shell backfill runs. Historical tracked events could retain a
-- calendar_event_id even when their canonical source/external_key already
-- belonged to a non-calendar producer. Detach only unambiguous terminal rows
-- that have no release-pipeline state under the legacy calendar identity; any
-- remaining mismatch aborts with an actionable report instead of being silently
-- skipped by the following v12 backfill.
begin;

do $$
declare
  conflict_report text;
begin
  update public.tracked_market_events t
  set calendar_event_id = null,
      updated_by = 'migration:repair-legacy-tracked-calendar-binding',
      updated_at = now()
  from public.calendar_events c
  where t.calendar_event_id = c.id
    and t.kind = 'earnings'
    and t.event_date is not null
    and t.status in ('completed', 'failed', 'cancelled')
    -- The calendar row still describes the same occurrence, so detaching the
    -- stale ownership link does not change instrument, kind or local date.
    and upper(replace(c.instrument, ' ', '')) = t.instrument
    and c.event_type = t.kind
    and c.scheduled_date = t.event_date
    -- The canonical producer identity is already non-calendar and must win.
    and c.source is distinct from t.source
    and t.external_key not like 'calendar:%'
    -- Never change canonical release identity after any dependent release,
    -- analysis, approval or execution state has been persisted. Such rows stay
    -- bound to the legacy identity and are reported by the conflict gate below
    -- for an explicit state-preserving repair.
    and not exists (
      select 1 from public.market_events s
      where s.event_id = ('calendar:' || c.id::text)
    )
    and not exists (
      select 1 from public.event_expectation_versions s
      where s.event_id = ('calendar:' || c.id::text)
    )
    and not exists (
      select 1 from public.event_official_release_sources s
      where s.event_id = ('calendar:' || c.id::text)
    )
    and not exists (
      select 1 from public.event_official_release_source_audit s
      where s.event_id = ('calendar:' || c.id::text)
    )
    and not exists (
      select 1 from public.event_source_documents s
      where s.event_id = ('calendar:' || c.id::text)
    )
    and not exists (
      select 1 from public.event_ai_analyses s
      where s.event_id = ('calendar:' || c.id::text)
    )
    and not exists (
      select 1 from public.event_ingestion_runs s
      where s.event_id = ('calendar:' || c.id::text)
    )
    and not exists (
      select 1 from public.event_strategy_approvals s
      where s.event_id = ('calendar:' || c.id::text)
    )
    and not exists (
      select 1 from public.event_paper_trade_event_claims s
      where s.event_id = ('calendar:' || c.id::text)
    )
    and not exists (
      select 1 from public.event_paper_trade_runs s
      where s.event_id = ('calendar:' || c.id::text)
    );

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
