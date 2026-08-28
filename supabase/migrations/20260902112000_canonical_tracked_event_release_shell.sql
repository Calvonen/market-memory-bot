-- Create the release-pipeline shell from canonical tracked-event identity instead
-- of requiring calendar ownership. Calendar-bound events retain their existing
-- calendar:<uuid> release identity for compatibility; calendar-less producers use
-- tracked:<tracked_event_id>. No consensus/KPI expectations are inferred.
begin;

create or replace function public.ensure_tracked_event_release_shell(
  input_tracked_event_id uuid
)
returns table (
  out_release_event_id text,
  out_action text
)
language plpgsql
security invoker
as $$
declare
  tracked_row public.tracked_market_events%rowtype;
  existing_market_event public.market_events%rowtype;
  release_event_id text;
  release_event_name text;
  expectation_exists boolean;
  shell_action text := 'noop_existing';
  calendar_shell record;
begin
  select * into tracked_row
  from public.tracked_market_events
  where id = input_tracked_event_id
  for update;

  if tracked_row.id is null then
    raise exception 'tracked_event_not_found' using errcode = 'P0002';
  end if;
  if tracked_row.kind <> 'earnings' then
    raise exception 'tracked_event_not_release_shell_eligible';
  end if;
  if tracked_row.event_date is null then
    raise exception 'tracked_event_release_date_required';
  end if;

  -- Preserve the already-deployed calendar release identity/source contract.
  if tracked_row.calendar_event_id is not null then
    select * into calendar_shell
    from public.ensure_calendar_release_shell(tracked_row.calendar_event_id);
    return query select
      calendar_shell.out_release_event_id::text,
      calendar_shell.out_action::text;
    return;
  end if;

  release_event_id := 'tracked:' || tracked_row.id::text;
  -- Keep the shell name grounded on immutable event identity fields. Human-facing
  -- title/company metadata may still be refined before monitoring and must not
  -- create a second release identity.
  release_event_name := tracked_row.instrument || ' ' || tracked_row.kind;

  select * into existing_market_event
  from public.market_events
  where event_id = release_event_id
  for update;

  if existing_market_event.event_id is null then
    insert into public.market_events (
      event_id,
      instrument,
      event_name,
      scheduled_date,
      status
    ) values (
      release_event_id,
      tracked_row.instrument,
      release_event_name,
      tracked_row.event_date,
      'scheduled'
    );
    shell_action := 'inserted_market_event';
  else
    if existing_market_event.instrument is distinct from tracked_row.instrument
       or existing_market_event.event_name is distinct from release_event_name
       or existing_market_event.scheduled_date is distinct from tracked_row.event_date then
      raise exception 'tracked_release_shell_identity_conflict';
    end if;
  end if;

  select exists (
    select 1
    from public.event_expectation_versions e
    where e.event_id = release_event_id
  ) into expectation_exists;

  if not expectation_exists then
    insert into public.event_expectation_versions (
      event_id,
      version,
      source_name,
      source_url,
      source_as_of,
      consensus,
      important_kpis,
      bull_case,
      base_case,
      bear_case,
      triggers,
      invalidation_conditions,
      change_note
    ) values (
      release_event_id,
      1,
      'tracked:' || tracked_row.source || ':automatic-release-shell',
      null,
      null,
      '{}'::jsonb,
      '[]'::jsonb,
      '[]'::jsonb,
      '[]'::jsonb,
      '[]'::jsonb,
      '{}'::jsonb,
      '[]'::jsonb,
      'automatic canonical tracked-event release shell; no consensus or KPI expectations inferred'
    );
    if shell_action = 'noop_existing' then
      shell_action := 'inserted_expectation';
    else
      shell_action := 'inserted';
    end if;
  end if;

  return query select release_event_id, shell_action;
end;
$$;

revoke all on function public.ensure_tracked_event_release_shell(uuid) from public;
grant execute on function public.ensure_tracked_event_release_shell(uuid) to service_role;

create or replace function public.ensure_tracked_event_release_shell_after_date_write()
returns trigger
language plpgsql
security invoker
as $$
begin
  perform * from public.ensure_tracked_event_release_shell(new.id);
  return new;
end;
$$;

revoke all on function public.ensure_tracked_event_release_shell_after_date_write() from public;

drop trigger if exists tracked_market_events_release_shell_after_date_write
  on public.tracked_market_events;
create trigger tracked_market_events_release_shell_after_date_write
  after insert or update of event_date on public.tracked_market_events
  for each row
  when (new.kind = 'earnings' and new.event_date is not null)
  execute function public.ensure_tracked_event_release_shell_after_date_write();

-- Backfill shells only for canonical tracked earnings that already have an
-- explicit local event_date. Never infer a date from event_at UTC.
do $$
declare
  target record;
begin
  for target in
    select id
    from public.tracked_market_events
    where kind = 'earnings'
      and event_date is not null
  loop
    perform * from public.ensure_tracked_event_release_shell(target.id);
  end loop;
end;
$$;

commit;
