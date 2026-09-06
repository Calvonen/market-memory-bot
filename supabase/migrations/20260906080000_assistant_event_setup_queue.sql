-- Proposal-only storage for assistant-prepared MarketAI event plans.
--
-- This migration deliberately does not materialize anything into canonical
-- tracked events, expectations, official release sources, trading tasks, risk,
-- or broker state. The assistant can stage a proposal; later backend code owns
-- validation/materialization and the user separately owns execution approval.

create table if not exists public.assistant_event_proposals (
  id uuid primary key default gen_random_uuid(),
  proposal_key text not null unique,
  payload jsonb not null,
  requested_execution_mode text not null default 'demo'
    check (requested_execution_mode in ('demo', 'live')),
  status text not null default 'ready_for_review'
    check (status in (
      'draft',
      'ready_for_review',
      'rejected',
      'approved_for_materialization',
      'materialized'
    )),
  created_by text not null default 'assistant',
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (btrim(proposal_key) <> ''),
  check (jsonb_typeof(payload) = 'object')
);

alter table public.assistant_event_proposals enable row level security;

revoke all on table public.assistant_event_proposals from anon, authenticated;
grant select, insert, update on table public.assistant_event_proposals to service_role;

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
    return new;
  end if;

  -- The primary key is the durable proposal identity and is never mutable.
  if new.id is distinct from old.id then
    raise exception 'assistant_proposal_id_is_immutable'
      using errcode = '55000';
  end if;

  -- Once review begins, the reviewed content and identity are frozen.
  if old.status <> 'draft' and (
    new.proposal_key is distinct from old.proposal_key
    or new.payload is distinct from old.payload
    or new.requested_execution_mode is distinct from old.requested_execution_mode
    or new.created_by is distinct from old.created_by
    or new.created_at is distinct from old.created_at
  ) then
    raise exception 'assistant_proposal_reviewed_content_is_immutable'
      using errcode = '55000';
  end if;

  if new.status is distinct from old.status then
    if not (
      (old.status = 'draft' and new.status = 'ready_for_review')
      or (old.status = 'ready_for_review' and new.status in ('approved_for_materialization', 'rejected'))
      or (old.status = 'approved_for_materialization' and new.status = 'materialized')
    ) then
      raise exception 'assistant_proposal_invalid_status_transition: % -> %', old.status, new.status
        using errcode = '22023';
    end if;
  end if;

  -- Reviewer metadata may be written exactly once, on the review decision.
  if old.status = 'ready_for_review'
     and new.status in ('approved_for_materialization', 'rejected')
     and new.status is distinct from old.status then
    if new.reviewed_by is null or btrim(new.reviewed_by) = '' or new.reviewed_at is null then
      raise exception 'assistant_proposal_review_metadata_required'
        using errcode = '22023';
    end if;
  elsif new.reviewed_by is distinct from old.reviewed_by
     or new.reviewed_at is distinct from old.reviewed_at then
    raise exception 'assistant_proposal_review_metadata_is_immutable'
      using errcode = '55000';
  end if;

  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.guard_assistant_event_proposal_lifecycle() from public;

drop trigger if exists assistant_event_proposals_lifecycle_guard
  on public.assistant_event_proposals;
create trigger assistant_event_proposals_lifecycle_guard
before insert or update on public.assistant_event_proposals
for each row execute function public.guard_assistant_event_proposal_lifecycle();

create index if not exists assistant_event_proposals_status_created_idx
  on public.assistant_event_proposals(status, created_at);

comment on table public.assistant_event_proposals is
  'Non-execution staging for assistant-prepared plans. Review freezes content and audit identity; canonical materialization and execution approval happen elsewhere.';
