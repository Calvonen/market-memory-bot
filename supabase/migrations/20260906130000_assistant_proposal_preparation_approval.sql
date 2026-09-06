-- Control-plane approval for assistant proposal preparation only.
-- This transition grants canonical materialization authority; it does not grant
-- DEMO/PAPER/LIVE trading authority and has no broker/task side effects.

begin;

create or replace function public.approve_assistant_event_proposal_for_materialization(
  input_proposal_id uuid,
  input_reviewer text
)
returns table (
  out_status text,
  out_reviewed_by text,
  out_review_round integer
)
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  proposal_row public.assistant_event_proposals%rowtype;
  canonical_reviewer text := btrim(input_reviewer);
begin
  if canonical_reviewer is null or canonical_reviewer = '' or length(canonical_reviewer) > 200 then
    raise exception 'assistant_proposal_reviewer_invalid' using errcode = '22023';
  end if;

  select * into proposal_row
  from public.assistant_event_proposals
  where id = input_proposal_id
  for update;

  if proposal_row.id is null then
    raise exception 'assistant_proposal_not_found' using errcode = 'P0002';
  end if;

  -- LIVE remains a visible but locked future mode. Reject it before changing
  -- review state so preparation approval can never become an indirect LIVE
  -- authority or leave a permanently approved proposal that cannot materialize.
  if proposal_row.requested_execution_mode <> 'demo' then
    raise exception 'assistant_proposal_live_locked' using errcode = '55000';
  end if;

  if proposal_row.status = 'ready_for_review' then
    update public.assistant_event_proposals
    set
      status = 'approved_for_materialization',
      reviewed_by = canonical_reviewer,
      reviewed_at = clock_timestamp()
    where id = input_proposal_id
      and status = 'ready_for_review'
    returning * into proposal_row;

    if not found then
      raise exception 'assistant_proposal_review_status_cas_failed' using errcode = '40001';
    end if;
  elsif proposal_row.status in ('approved_for_materialization', 'materialized') then
    if proposal_row.reviewed_by is distinct from canonical_reviewer then
      raise exception 'assistant_proposal_already_approved_by_different_reviewer' using errcode = '55000';
    end if;
  else
    raise exception 'assistant_proposal_not_ready_for_preparation_approval: %', proposal_row.status
      using errcode = '55000';
  end if;

  if proposal_row.review_round < 1 then
    raise exception 'assistant_proposal_missing_review_snapshot' using errcode = '55000';
  end if;

  return query select
    proposal_row.status,
    proposal_row.reviewed_by,
    proposal_row.review_round;
end;
$$;

revoke all on function public.approve_assistant_event_proposal_for_materialization(uuid, text)
  from public, anon, authenticated;
grant execute on function public.approve_assistant_event_proposal_for_materialization(uuid, text)
  to service_role;

commit;
