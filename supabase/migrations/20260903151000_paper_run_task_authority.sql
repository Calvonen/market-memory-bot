-- Bind canonical PAPER execution authority to the durable paper-run audit.
-- Legacy paper runs remain valid with a null task_id; the tracked-event
-- orchestration introduced after canonical trading tasks always uses the
-- task-aware claim RPC below.

alter table public.event_paper_trade_runs
  add column if not exists task_id uuid null
    references public.trading_tasks(id) on delete restrict;

alter table public.event_paper_trade_event_claims
  add column if not exists task_id uuid null
    references public.trading_tasks(id) on delete restrict;

create unique index if not exists event_paper_trade_runs_task_uidx
  on public.event_paper_trade_runs(task_id)
  where task_id is not null;

create index if not exists event_paper_trade_claims_task_idx
  on public.event_paper_trade_event_claims(task_id)
  where task_id is not null;

create or replace function public.claim_event_paper_run_for_task(
  input_event_id text,
  input_analysis_id uuid,
  input_task_id uuid,
  input_claim_token uuid,
  input_lease_seconds integer
)
returns setof public.event_paper_trade_event_claims
language plpgsql
security invoker
set search_path = public
as $$
declare
  task_row public.trading_tasks%rowtype;
  claimed public.event_paper_trade_event_claims%rowtype;
begin
  if input_task_id is null then
    raise exception 'paper_run_task_missing';
  end if;

  -- Keep the same event advisory lock through authority validation and claim
  -- binding. A concurrent cancellation may not commit between these steps.
  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 0));

  select * into task_row
  from public.trading_tasks
  where id = input_task_id
  for share;

  if not found then
    raise exception 'paper_run_task_not_found';
  end if;
  if task_row.state <> 'approved' then
    raise exception 'paper_run_task_not_approved';
  end if;
  if task_row.mode <> 'PAPER' then
    raise exception 'paper_run_task_not_paper';
  end if;
  if task_row.source_event_id <> input_event_id then
    raise exception 'paper_run_task_event_mismatch';
  end if;

  select * into claimed
  from public.claim_event_paper_run(
    input_event_id,
    input_analysis_id,
    input_claim_token,
    input_lease_seconds
  )
  limit 1;

  if not found then
    raise exception 'paper_run_task_claim_missing';
  end if;

  -- A terminal owner is historical truth. Never attach a replacement task to
  -- it merely because a later runner asked for the same event.
  if claimed.terminal_status is not null then
    return next claimed;
    return;
  end if;

  if claimed.analysis_id = input_analysis_id
     and claimed.claim_token = input_claim_token then
    if claimed.task_id is not null and claimed.task_id <> input_task_id then
      raise exception 'paper_run_task_claim_conflict';
    end if;

    update public.event_paper_trade_event_claims
    set task_id = input_task_id, updated_at = clock_timestamp()
    where event_id = input_event_id
      and analysis_id = input_analysis_id
      and claim_token = input_claim_token
    returning * into claimed;
  end if;

  return next claimed;
end;
$$;

create or replace function public.bind_event_paper_run_task_from_claim()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  claimed_task_id uuid;
begin
  select task_id into claimed_task_id
  from public.event_paper_trade_event_claims
  where event_id = new.event_id
    and analysis_id = new.analysis_id
  limit 1;

  if claimed_task_id is null then
    -- Compatibility path for legacy/non-task paper execution.
    return new;
  end if;

  if new.task_id is not null and new.task_id <> claimed_task_id then
    raise exception 'paper_run_task_audit_conflict';
  end if;

  if tg_op = 'UPDATE'
     and old.task_id is not null
     and old.task_id <> claimed_task_id then
    -- Do not rewrite an existing run from a cancelled/replaced approval. The
    -- caller must create a genuinely distinct execution record instead of
    -- making old authorization history indistinguishable from the replacement.
    raise exception 'paper_run_task_replacement_conflict';
  end if;

  new.task_id := claimed_task_id;
  return new;
end;
$$;

drop trigger if exists bind_event_paper_run_task_from_claim
  on public.event_paper_trade_runs;
create trigger bind_event_paper_run_task_from_claim
before insert or update on public.event_paper_trade_runs
for each row execute function public.bind_event_paper_run_task_from_claim();

revoke all on function public.claim_event_paper_run_for_task(text, uuid, uuid, uuid, integer)
  from public, anon, authenticated;
grant execute on function public.claim_event_paper_run_for_task(text, uuid, uuid, uuid, integer)
  to service_role;
