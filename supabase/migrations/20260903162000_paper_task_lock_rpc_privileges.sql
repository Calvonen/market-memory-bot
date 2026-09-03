-- Keep direct trading_tasks writes fail-closed for service_role while allowing
-- the approved PAPER worker RPC boundary to take row locks required by
-- SELECT ... FOR SHARE / FOR UPDATE.
--
-- These functions are owned by postgres and already pin search_path. Running
-- them as SECURITY DEFINER preserves the intended RPC-only write boundary
-- without granting UPDATE directly on public.trading_tasks.

alter function public.revalidate_event_paper_run_task_lease(
  text, uuid, uuid, integer, uuid, integer
) security definer;

alter function public.save_event_paper_trade_result_for_task(jsonb)
  security definer;

revoke all on function public.revalidate_event_paper_run_task_lease(
  text, uuid, uuid, integer, uuid, integer
) from public, anon, authenticated;
grant execute on function public.revalidate_event_paper_run_task_lease(
  text, uuid, uuid, integer, uuid, integer
) to service_role;

revoke all on function public.save_event_paper_trade_result_for_task(jsonb)
  from public, anon, authenticated;
grant execute on function public.save_event_paper_trade_result_for_task(jsonb)
  to service_role;

-- Preserve the table-level fail-closed boundary explicitly.
revoke insert, update, delete, truncate, references, trigger
  on table public.trading_tasks
  from service_role;
grant select on table public.trading_tasks to service_role;
