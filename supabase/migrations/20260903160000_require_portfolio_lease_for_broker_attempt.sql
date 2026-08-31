-- Close the remaining direct service-role path around account-wide PAPER
-- portfolio serialization. The audited broker-attempt primitive stays callable
-- by SECURITY DEFINER wrappers, but service_role may reserve a new attempt only
-- through the singleton portfolio-lease-aware transaction.

revoke execute on function public.begin_event_paper_broker_attempt(
  text, uuid, uuid, integer, uuid, uuid, integer, jsonb, jsonb
) from service_role;

-- Keep the only production reservation entrypoint explicit. This wrapper is
-- SECURITY DEFINER and validates/renews the singleton portfolio lease in the
-- same transaction before delegating to the internal audited primitive.
alter function public.begin_event_paper_broker_attempt_with_portfolio_lease(
  text, uuid, uuid, integer, uuid, uuid, integer, jsonb, jsonb, uuid, integer
) security definer;
alter function public.begin_event_paper_broker_attempt_with_portfolio_lease(
  text, uuid, uuid, integer, uuid, uuid, integer, jsonb, jsonb, uuid, integer
) set search_path = public, pg_temp;

revoke all on function public.begin_event_paper_broker_attempt_with_portfolio_lease(
  text, uuid, uuid, integer, uuid, uuid, integer, jsonb, jsonb, uuid, integer
) from public, anon, authenticated;
grant execute on function public.begin_event_paper_broker_attempt_with_portfolio_lease(
  text, uuid, uuid, integer, uuid, uuid, integer, jsonb, jsonb, uuid, integer
) to service_role;
