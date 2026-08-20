alter table public.event_paper_trade_runs
  add column if not exists completed_components jsonb null,
  add column if not exists confirmation_deadline_at timestamptz null,
  add column if not exists expired_at timestamptz null;

alter table public.event_paper_trade_runs
  drop constraint if exists event_paper_trade_runs_status_check;

alter table public.event_paper_trade_runs
  add constraint event_paper_trade_runs_status_check
  check (status in ('waiting_confirmation', 'paper_executed', 'expired_no_trade'));

alter table public.event_paper_trade_runs
  add constraint event_paper_trade_runs_expiry_check
  check (status <> 'expired_no_trade' or expired_at is not null);

create index if not exists event_paper_trade_runs_open_deadline_idx
  on public.event_paper_trade_runs(confirmation_deadline_at)
  where status = 'waiting_confirmation';

comment on column public.event_paper_trade_runs.confirmation_deadline_at is
  'Temporary event/session policy: release-day LSE close plus configurable grace, persisted in UTC.';
