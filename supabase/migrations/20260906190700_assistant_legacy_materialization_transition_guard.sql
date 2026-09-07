-- Close the deployment race between the legacy materialization check and the
-- durable-lineage migration. The pre-v5 API may remain live while migrations
-- are applied out of band, so a count alone is not sufficient.
--
-- Install a persistent transition guard under an ACCESS EXCLUSIVE table lock,
-- then recheck legacy state while that lock is held. Before durable lineage
-- exists every new transition to `materialized` fails closed. Once lineage is
-- installed, the transition is accepted only when the exact proposal already
-- has a durable binding and the immutable receipt guard is active.

begin;

create or replace function public.guard_assistant_proposal_materialized_lineage()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  binding_exists boolean := false;
  receipt_guard_ready boolean := false;
begin
  if new.status is distinct from 'materialized'
     or old.status is not distinct from 'materialized' then
    return new;
  end if;

  if to_regclass('public.assistant_proposal_materializations') is null then
    raise exception 'assistant_materialization_requires_durable_lineage'
      using errcode = '55000';
  end if;

  execute
    'select exists (select 1 from public.assistant_proposal_materializations where proposal_id = $1)'
    into binding_exists
    using new.id;

  if not binding_exists then
    raise exception 'assistant_materialization_requires_durable_lineage'
      using errcode = '55000';
  end if;

  -- A durable binding alone is not enough during an out-of-band upgrade. The
  -- 1910 migration can install the assistant finalizer before 1920 installs the
  -- immutable receipt snapshot guard. Keep terminal materialization closed until
  -- that guard is present, origin-enabled, attached to the expected function,
  -- and fires BEFORE INSERT FOR EACH ROW.
  if to_regclass('public.event_strategy_approvals') is not null
     and to_regprocedure('public.guard_assistant_proposal_receipt_snapshot()') is not null then
    select exists (
      select 1
      from pg_trigger
      where tgrelid = 'public.event_strategy_approvals'::regclass
        and tgname = 'assistant_proposal_receipt_snapshot_guard'
        and not tgisinternal
        and tgenabled in ('O', 'A')
        and tgfoid = to_regprocedure('public.guard_assistant_proposal_receipt_snapshot()')
        and tgtype = 7
    ) into receipt_guard_ready;
  end if;

  if not receipt_guard_ready then
    raise exception 'assistant_materialization_requires_receipt_snapshot_guard'
      using errcode = '55000';
  end if;

  return new;
end;
$$;

revoke all on function public.guard_assistant_proposal_materialized_lineage()
  from public, anon, authenticated;

-- Serialize with any pre-v5 finalizer transaction. Transactions already in
-- flight must finish before this lock is granted; their committed state is then
-- visible to the check below. While the lock is held the guard is installed,
-- so no later legacy finalizer can create a post-check materialization.
lock table public.assistant_event_proposals in access exclusive mode;

drop trigger if exists assistant_proposal_materialized_lineage_guard
  on public.assistant_event_proposals;
create trigger assistant_proposal_materialized_lineage_guard
before update of status on public.assistant_event_proposals
for each row execute function public.guard_assistant_proposal_materialized_lineage();

do $$
declare
  legacy_count integer;
begin
  select count(*) into legacy_count
  from public.assistant_event_proposals
  where status = 'materialized';

  if legacy_count > 0 then
    raise exception
      'assistant_legacy_materializations_require_verified_backfill: % materialized proposal(s)',
      legacy_count
      using errcode = '55000';
  end if;
end;
$$;

commit;
