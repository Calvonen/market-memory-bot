alter table public.event_paper_trade_runs
  drop constraint if exists event_paper_trade_runs_status_check;

alter table public.event_paper_trade_runs
  add constraint event_paper_trade_runs_status_check
  check (status in (
    'observing_post_release',
    'waiting_confirmation',
    'paper_executed',
    'expired_no_trade',
    'superseded'
  ));

drop index if exists public.event_paper_trade_runs_open_deadline_idx;
create index event_paper_trade_runs_open_deadline_idx
  on public.event_paper_trade_runs(confirmation_deadline_at)
  where status in ('observing_post_release', 'waiting_confirmation');

comment on column public.event_paper_trade_runs.status is
  'PAPER lifecycle state. observing_post_release is a non-terminal earnings observation state before confirmation is eligible.';
