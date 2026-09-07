-- Fail closed before durable assistant lineage is introduced.
--
-- A proposal materialized by the pre-lineage assistant flow cannot safely be
-- treated as an ordinary manual event while the durable binding table is still
-- absent. Require an explicit audited backfill instead of guessing lineage.

do $$
declare
  legacy_count integer;
begin
  select count(*) into legacy_count
  from public.assistant_event_proposals p
  where p.status = 'materialized'
    and exists (
      select 1
      from public.event_strategy_approvals a
      where a.approved_via = 'assistant_proposal:' || p.id::text
    );

  if legacy_count > 0 then
    raise exception
      'assistant_legacy_materializations_require_verified_backfill: % materialized proposal(s)',
      legacy_count
      using errcode = '55000';
  end if;
end;
$$;
