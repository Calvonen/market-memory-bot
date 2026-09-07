-- Canonicalize queued assistant proposal symbols before the v5 receipt-hardening
-- migration creates its normalizing trigger. `ready_for_review` content is
-- otherwise immutable under the lifecycle guard, so perform this one-time
-- migration backfill with that single guard disabled inside the same transaction.

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
-- reject a content-preserving identity normalization on ready_for_review rows.
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

commit;
