-- Make the assistant_proposal:* receipt namespace enforceable below every
-- runtime client, not only at the generic RPC call site. Direct service-role
-- INSERT into the approval audit table would otherwise bypass the namespace
-- guard entirely.

begin;

-- The reviewed RPCs are the only runtime writers of approval receipts. They
-- already pin search_path and validate/CAS all caller-controlled identity before
-- inserting, so owner-privileged execution lets us revoke the underlying table
-- INSERT privilege without widening the public surface.
alter function public.approve_strategy_draft(
  text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb,
  text, text, text, text
) security definer;

alter function public.approve_assistant_proposal_strategy_draft(
  uuid, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb,
  text, text, text, text, text, text, text, integer, boolean, text
) security definer;

revoke insert on table public.event_strategy_approvals from service_role;

-- Keep read access for deterministic retry/recovery lookups.
grant select on table public.event_strategy_approvals to service_role;

commit;
