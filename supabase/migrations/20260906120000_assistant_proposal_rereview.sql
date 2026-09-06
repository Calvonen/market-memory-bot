-- Preserve assistant proposal canonical identity while allowing an explicitly
-- approved proposal to be re-opened for review after an expectation-version conflict.
-- Every review decision is snapshotted before the proposal can be edited again.

alter table public.assistant_event_proposals
  add column if not exists review_round integer not null default 0
    check (review_round >= 0);

create table if not exists public.assistant_event_proposal_reviews (
  proposal_id uuid not null references public.assistant_event_proposals(id) on delete restrict,
  review_round integer not null check (review_round > 0),
  decision text not null check (decision in ('approved_for_materialization', 'rejected')),
  proposal_key text not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  requested_execution_mode text not null check (requested_execution_mode in ('demo', 'live')),
  reviewed_by text not null check (btrim(reviewed_by) <> ''),
  reviewed_at timestamptz not null,
  recorded_at timestamptz not null default now(),
  primary key (proposal_id, review_round)
);

alter table public.assistant_event_proposal_reviews enable row level security;
revoke all on table public.assistant_event_proposal_reviews from anon, authenticated;
grant select, insert on table public.assistant_event_proposal_reviews to service_role;

-- Backfill proposals that were reviewed before review rounds existed.
update public.assistant_event_proposals
set review_round = 1
where review_round = 0
  and status in ('approved_for_materialization', 'rejected', 'materialized')
  and reviewed_by is not null
  and reviewed_at is not null;

insert into public.assistant_event_proposal_reviews (
  proposal_id,
  review_round,
  decision,
  proposal_key,
  payload,
  requested_execution_mode,
  reviewed_by,
  reviewed_at
)
select
  id,
  review_round,
  case when status = 'rejected' then 'rejected' else 'approved_for_materialization' end,
  proposal_key,
  payload,
  requested_execution_mode,
  reviewed_by,
  reviewed_at
from public.assistant_event_proposals
where review_round > 0
  and reviewed_by is not null
  and reviewed_at is not null
on conflict (proposal_id, review_round) do nothing;

create or replace function public.guard_assistant_event_proposal_lifecycle()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if tg_op = 'INSERT' then
    if new.status not in ('draft', 'ready_for_review') then
      raise exception 'assistant_proposal_invalid_initial_status: %', new.status
        using errcode = '22023';
    end if;
    if new.reviewed_by is not null or new.reviewed_at is not null then
      raise exception 'assistant_proposal_review_metadata_not_allowed_on_insert'
        using errcode = '22023';
    end if;
    if new.review_round <> 0 then
      raise exception 'assistant_proposal_review_round_not_allowed_on_insert'
        using errcode = '22023';
    end if;
    return new;
  end if;

  if new.id is distinct from old.id then
    raise exception 'assistant_proposal_id_is_immutable'
      using errcode = '55000';
  end if;

  -- The canonical external key and creation identity never change, including
  -- during re-review. This is what prevents duplicate tracked events.
  if new.proposal_key is distinct from old.proposal_key
     or new.created_by is distinct from old.created_by
     or new.created_at is distinct from old.created_at then
    raise exception 'assistant_proposal_identity_is_immutable'
      using errcode = '55000';
  end if;

  -- Reviewed content remains frozen outside draft. Re-opening first changes only
  -- lifecycle/reviewer metadata; content can be edited only on a later draft update.
  if old.status <> 'draft' and (
    new.payload is distinct from old.payload
    or new.requested_execution_mode is distinct from old.requested_execution_mode
  ) then
    raise exception 'assistant_proposal_reviewed_content_is_immutable'
      using errcode = '55000';
  end if;

  if new.status is distinct from old.status then
    if not (
      (old.status = 'draft' and new.status = 'ready_for_review')
      or (old.status = 'ready_for_review' and new.status in ('approved_for_materialization', 'rejected'))
      or (old.status = 'approved_for_materialization' and new.status in ('materialized', 'draft'))
    ) then
      raise exception 'assistant_proposal_invalid_status_transition: % -> %', old.status, new.status
        using errcode = '22023';
    end if;
  end if;

  if old.status = 'ready_for_review'
     and new.status in ('approved_for_materialization', 'rejected')
     and new.status is distinct from old.status then
    if new.reviewed_by is null or btrim(new.reviewed_by) = '' or new.reviewed_at is null then
      raise exception 'assistant_proposal_review_metadata_required'
        using errcode = '22023';
    end if;
    new.review_round := old.review_round + 1;
  elsif old.status = 'approved_for_materialization'
     and new.status = 'draft'
     and new.status is distinct from old.status then
    -- Explicit re-review reset. Keep the historical review_round; the next
    -- decision increments it and is recorded as a new immutable snapshot.
    if new.payload is distinct from old.payload
       or new.requested_execution_mode is distinct from old.requested_execution_mode then
      raise exception 'assistant_proposal_rereview_must_reopen_before_editing'
        using errcode = '55000';
    end if;
    new.reviewed_by := null;
    new.reviewed_at := null;
    new.review_round := old.review_round;
  else
    if new.reviewed_by is distinct from old.reviewed_by
       or new.reviewed_at is distinct from old.reviewed_at then
      raise exception 'assistant_proposal_review_metadata_is_immutable'
        using errcode = '55000';
    end if;
    if new.review_round is distinct from old.review_round then
      raise exception 'assistant_proposal_review_round_is_managed'
        using errcode = '55000';
    end if;
  end if;

  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.guard_assistant_event_proposal_lifecycle() from public;

create or replace function public.audit_assistant_event_proposal_review()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if old.status = 'ready_for_review'
     and new.status in ('approved_for_materialization', 'rejected')
     and new.status is distinct from old.status then
    insert into public.assistant_event_proposal_reviews (
      proposal_id,
      review_round,
      decision,
      proposal_key,
      payload,
      requested_execution_mode,
      reviewed_by,
      reviewed_at
    ) values (
      new.id,
      new.review_round,
      new.status,
      new.proposal_key,
      new.payload,
      new.requested_execution_mode,
      new.reviewed_by,
      new.reviewed_at
    );
  end if;
  return new;
end;
$$;

revoke all on function public.audit_assistant_event_proposal_review() from public;

drop trigger if exists assistant_event_proposals_review_audit
  on public.assistant_event_proposals;
create trigger assistant_event_proposals_review_audit
after update on public.assistant_event_proposals
for each row execute function public.audit_assistant_event_proposal_review();

comment on table public.assistant_event_proposal_reviews is
  'Immutable snapshots of assistant proposal review decisions, retained across explicit re-review cycles.';
