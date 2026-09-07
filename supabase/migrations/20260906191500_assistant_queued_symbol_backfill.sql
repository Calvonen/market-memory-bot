-- Canonicalize queued assistant proposal symbols before the v5 receipt-hardening
-- migration. Also install the same normalizer trigger here so live writes between
-- this migration and 1920 cannot reintroduce noncanonical ready_for_review rows.

begin;

create or replace function public.assistant_normalize_symbol(input_value text)
returns text
language sql
immutable
strict
set search_path = pg_catalog
as $$
  select upper(regexp_replace(input_value, '[[:space:]]+', '', 'g'));
$$;

revoke all on function public.assistant_normalize_symbol(text)
  from public, anon, authenticated;
grant execute on function public.assistant_normalize_symbol(text) to service_role;

-- Reject malformed/conflicting queued identities rather than silently choosing
-- one side of the reviewed intent.
do $$
declare
  invalid_count integer;
begin
  select count(*) into invalid_count
  from public.assistant_event_proposals p
  where p.status in ('draft', 'ready_for_review')
    and (
      nullif(public.assistant_normalize_symbol(p.payload ->> 'instrument'), '') is null
      or nullif(public.assistant_normalize_symbol(p.payload #>> '{strategy,instrument}'), '') is null
      or public.assistant_normalize_symbol(p.payload ->> 'instrument')
         is distinct from public.assistant_normalize_symbol(p.payload #>> '{strategy,instrument}')
    );

  if invalid_count > 0 then
    raise exception 'assistant_queued_proposal_symbol_identity_invalid: % row(s)', invalid_count
      using errcode = '55000';
  end if;
end;
$$;

-- This is a migration-only canonicalization of rows that have not yet produced
-- an immutable approved snapshot. Disable only the lifecycle trigger that would
-- reject identity normalization on an existing ready_for_review row.
alter table public.assistant_event_proposals
  disable trigger assistant_event_proposals_lifecycle_guard;

update public.assistant_event_proposals p
set payload = jsonb_set(
  jsonb_set(
    p.payload,
    '{instrument}',
    to_jsonb(public.assistant_normalize_symbol(p.payload ->> 'instrument')),
    false
  ),
  '{strategy,instrument}',
  to_jsonb(public.assistant_normalize_symbol(p.payload #>> '{strategy,instrument}')),
  false
)
where p.status in ('draft', 'ready_for_review')
  and (
    p.payload ->> 'instrument'
      is distinct from public.assistant_normalize_symbol(p.payload ->> 'instrument')
    or p.payload #>> '{strategy,instrument}'
      is distinct from public.assistant_normalize_symbol(p.payload #>> '{strategy,instrument}')
  );

alter table public.assistant_event_proposals
  enable trigger assistant_event_proposals_lifecycle_guard;

-- Install normalization before this migration commits. The trigger name sorts
-- before the lifecycle guard, so any live INSERT/UPDATE in the gap before 1920
-- is canonicalized first and cannot recreate the queued-row migration failure.
create or replace function public.normalize_assistant_proposal_symbols()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  normalized_top text;
  normalized_strategy text;
begin
  if new.status not in ('draft', 'ready_for_review') then
    return new;
  end if;

  normalized_top := public.assistant_normalize_symbol(new.payload ->> 'instrument');
  normalized_strategy := public.assistant_normalize_symbol(new.payload #>> '{strategy,instrument}');

  if nullif(normalized_top, '') is null or nullif(normalized_strategy, '') is null then
    raise exception 'assistant_proposal_instrument_missing' using errcode = '22023';
  end if;
  if normalized_top is distinct from normalized_strategy then
    raise exception 'assistant_proposal_strategy_instrument_conflict' using errcode = '55000';
  end if;

  new.payload := jsonb_set(new.payload, '{instrument}', to_jsonb(normalized_top), false);
  new.payload := jsonb_set(new.payload, '{strategy,instrument}', to_jsonb(normalized_strategy), false);
  return new;
end;
$$;

revoke all on function public.normalize_assistant_proposal_symbols()
  from public, anon, authenticated;

drop trigger if exists aa_normalize_assistant_proposal_symbols
  on public.assistant_event_proposals;
create trigger aa_normalize_assistant_proposal_symbols
before insert or update on public.assistant_event_proposals
for each row execute function public.normalize_assistant_proposal_symbols();

commit;
