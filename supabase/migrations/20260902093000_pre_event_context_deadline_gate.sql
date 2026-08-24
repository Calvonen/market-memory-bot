begin;

create or replace function public.capture_tracked_market_event_pre_event_context_if_current(
  input_event_id uuid,
  input_pre_event_market_context jsonb,
  input_market_timezone text,
  input_actor text,
  input_expected_updated_at timestamptz
)
returns public.tracked_market_events
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
begin
  if input_expected_updated_at is null then
    raise exception 'input_expected_updated_at is required';
  end if;

  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;

  -- Preserve exact retry idempotency if a valid pre-event capture committed
  -- but its response was lost. This branch does not create a new snapshot.
  if existing_row.pre_event_market_context = input_pre_event_market_context then
    select * into saved_row
    from public.capture_tracked_market_event_pre_event_context(
      input_event_id,
      input_pre_event_market_context,
      input_market_timezone,
      input_actor
    );
    return saved_row;
  end if;

  -- The worker preparation can run in a background thread that cannot be
  -- cancelled safely. Enforce the event deadline while the row is locked so a
  -- calendar/Yahoo call that finishes after event_at can never persist a new
  -- pre-event context, even if the RPC itself was started before event_at.
  if pg_catalog.clock_timestamp() >= existing_row.event_at then
    raise exception 'tracked_market_event_pre_event_context_deadline_passed';
  end if;

  if existing_row.updated_at is distinct from input_expected_updated_at then
    raise exception 'tracked_market_event_version_conflict';
  end if;

  select * into saved_row
  from public.capture_tracked_market_event_pre_event_context(
    input_event_id,
    input_pre_event_market_context,
    input_market_timezone,
    input_actor
  );

  return saved_row;
end;
$$;

revoke all on function public.capture_tracked_market_event_pre_event_context_if_current(
  uuid, jsonb, text, text, timestamptz
) from public;
grant execute on function public.capture_tracked_market_event_pre_event_context_if_current(
  uuid, jsonb, text, text, timestamptz
) to service_role;

commit;
