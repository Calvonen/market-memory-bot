-- Fail closed before durable assistant lineage is introduced.
--
-- A proposal materialized by the pre-lineage assistant flow cannot safely be
-- treated as an ordinary manual event while the durable binding table is still
-- absent. Require an explicit audited backfill instead of guessing lineage.
-- Reject every legacy materialized proposal regardless of whether an approval
-- receipt happens to exist: the receipt alone is not independently verified
-- durable proposal->event lineage.
do $$
declare
  legacy_count integer;
begin
  select count(*) into legacy_count
  from public.assistant_event_proposals p
  where p.status = 'materialized';

  if legacy_count > 0 then
    raise exception
      'assistant_legacy_materializations_require_verified_backfill: % materialized proposal(s)',
      legacy_count
      using errcode = '55000';
  end if;
end;
$$;
