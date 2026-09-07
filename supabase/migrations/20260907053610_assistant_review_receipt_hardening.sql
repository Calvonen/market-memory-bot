-- Production replay marker for assistant review receipt hardening.
--
-- Production had already applied migrations through 20260906191500 when PR #332
-- was merged. The equivalent 20260906192000 receipt hardening was therefore
-- replayed through the Supabase deployment API under this generated migration
-- version. Fresh databases receive the same schema from 20260906192000; this
-- marker keeps repository and production migration histories aligned without
-- applying the hardening twice.

begin;

-- Fail closed if this marker is ever reached without the receipt hardening that
-- 20260906192000 installs on a fresh database.
do $$
begin
  if to_regprocedure('public.guard_assistant_proposal_receipt_snapshot()') is null
     or not exists (
       select 1
       from pg_trigger
       where tgrelid = 'public.event_strategy_approvals'::regclass
         and tgname = 'assistant_proposal_receipt_snapshot_guard'
         and not tgisinternal
         and tgenabled in ('O', 'A')
         and tgfoid = to_regprocedure('public.guard_assistant_proposal_receipt_snapshot()')
         and tgtype = 7
     ) then
    raise exception 'assistant_receipt_hardening_replay_missing' using errcode = '55000';
  end if;
end;
$$;

commit;
