begin;

alter table public.tracked_market_events
  add column if not exists pre_event_market_context jsonb;

comment on column public.tracked_market_events.pre_event_market_context is
  'Immutable v1 snapshot of the last complete market session and prior close captured before the tracked event.';

create or replace function public.is_valid_pre_event_market_context_v1(snapshot jsonb)
returns boolean
language plpgsql
immutable
as $$
declare
  session_date_value date;
  previous_session_date_value date;
  open_value numeric;
  high_value numeric;
  low_value numeric;
  close_value numeric;
  previous_close_value numeric;
  session_return_value numeric;
  close_to_close_return_value numeric;
  expected_session_return numeric;
  expected_close_to_close_return numeric;
  expected_direction text;
  return_tolerance constant numeric := 0.000000000001;
begin
  if snapshot is null or jsonb_typeof(snapshot) <> 'object' then
    return false;
  end if;

  if (select count(*) from jsonb_object_keys(snapshot)) <> 11 then
    return false;
  end if;

  if not (snapshot ? 'schema_version')
     or jsonb_typeof(snapshot -> 'schema_version') <> 'number'
     or (snapshot ->> 'schema_version')::numeric <> 1 then
    return false;
  end if;

  if not (snapshot ? 'session_date')
     or jsonb_typeof(snapshot -> 'session_date') <> 'string'
     or not (snapshot ? 'previous_session_date')
     or jsonb_typeof(snapshot -> 'previous_session_date') <> 'string'
     or not (snapshot ? 'open_price')
     or jsonb_typeof(snapshot -> 'open_price') <> 'string'
     or not (snapshot ? 'high_price')
     or jsonb_typeof(snapshot -> 'high_price') <> 'string'
     or not (snapshot ? 'low_price')
     or jsonb_typeof(snapshot -> 'low_price') <> 'string'
     or not (snapshot ? 'close_price')
     or jsonb_typeof(snapshot -> 'close_price') <> 'string'
     or not (snapshot ? 'previous_close_price')
     or jsonb_typeof(snapshot -> 'previous_close_price') <> 'string'
     or not (snapshot ? 'session_return_pct')
     or jsonb_typeof(snapshot -> 'session_return_pct') <> 'string'
     or not (snapshot ? 'close_to_close_return_pct')
     or jsonb_typeof(snapshot -> 'close_to_close_return_pct') <> 'string'
     or not (snapshot ? 'close_to_close_direction')
     or jsonb_typeof(snapshot -> 'close_to_close_direction') <> 'string' then
    return false;
  end if;

  begin
    session_date_value := (snapshot ->> 'session_date')::date;
    previous_session_date_value := (snapshot ->> 'previous_session_date')::date;
    open_value := (snapshot ->> 'open_price')::numeric;
    high_value := (snapshot ->> 'high_price')::numeric;
    low_value := (snapshot ->> 'low_price')::numeric;
    close_value := (snapshot ->> 'close_price')::numeric;
    previous_close_value := (snapshot ->> 'previous_close_price')::numeric;
    session_return_value := (snapshot ->> 'session_return_pct')::numeric;
    close_to_close_return_value := (snapshot ->> 'close_to_close_return_pct')::numeric;
  exception when others then
    return false;
  end;

  if session_date_value::text <> snapshot ->> 'session_date'
     or previous_session_date_value::text <> snapshot ->> 'previous_session_date' then
    return false;
  end if;

  if previous_session_date_value >= session_date_value then
    return false;
  end if;

  -- PostgreSQL numeric accepts NaN/Infinity; the Python producer deliberately
  -- rejects them, so mirror that fail-closed contract before comparisons.
  if open_value::text in ('NaN', 'Infinity', '-Infinity')
     or high_value::text in ('NaN', 'Infinity', '-Infinity')
     or low_value::text in ('NaN', 'Infinity', '-Infinity')
     or close_value::text in ('NaN', 'Infinity', '-Infinity')
     or previous_close_value::text in ('NaN', 'Infinity', '-Infinity')
     or session_return_value::text in ('NaN', 'Infinity', '-Infinity')
     or close_to_close_return_value::text in ('NaN', 'Infinity', '-Infinity') then
    return false;
  end if;

  if open_value <= 0 or high_value <= 0 or low_value <= 0
     or close_value <= 0 or previous_close_value <= 0 then
    return false;
  end if;

  if high_value < greatest(open_value, close_value, low_value)
     or low_value > least(open_value, close_value, high_value) then
    return false;
  end if;

  expected_session_return := ((close_value / open_value) - 1) * 100;
  expected_close_to_close_return := ((close_value / previous_close_value) - 1) * 100;

  -- Python Decimal and PostgreSQL numeric use different division precision for
  -- repeating ratios. Validate parity to an explicit 1e-12 percentage-point
  -- tolerance rather than demanding impossible exact cross-runtime equality.
  if abs(session_return_value - expected_session_return) > return_tolerance
     or abs(close_to_close_return_value - expected_close_to_close_return) > return_tolerance then
    return false;
  end if;

  expected_direction := case
    when close_to_close_return_value > 0 then 'up'
    when close_to_close_return_value < 0 then 'down'
    else 'flat'
  end;

  if snapshot ->> 'close_to_close_direction' <> expected_direction then
    return false;
  end if;

  return true;
exception when others then
  return false;
end;
$$;

revoke all on function public.is_valid_pre_event_market_context_v1 from public;
grant execute on function public.is_valid_pre_event_market_context_v1 to service_role;

alter table public.tracked_market_events
  drop constraint if exists tracked_market_events_pre_event_market_context_valid;

alter table public.tracked_market_events
  add constraint tracked_market_events_pre_event_market_context_valid
  check (
    pre_event_market_context is null
    or public.is_valid_pre_event_market_context_v1(pre_event_market_context)
  );

create or replace function public.enforce_pre_event_market_context_immutable()
returns trigger
language plpgsql
as $$
begin
  if old.pre_event_market_context is not null
     and new.pre_event_market_context is distinct from old.pre_event_market_context then
    raise exception 'pre_event_market_context_immutable';
  end if;
  return new;
end;
$$;

revoke all on function public.enforce_pre_event_market_context_immutable from public;
grant execute on function public.enforce_pre_event_market_context_immutable to service_role;

drop trigger if exists tracked_market_events_pre_event_context_immutable
  on public.tracked_market_events;

create trigger tracked_market_events_pre_event_context_immutable
  before update on public.tracked_market_events
  for each row
  execute function public.enforce_pre_event_market_context_immutable();

create or replace function public.capture_tracked_market_event_pre_event_context(
  input_event_id uuid,
  input_pre_event_market_context jsonb,
  input_market_timezone text,
  input_actor text
)
returns public.tracked_market_events
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
  event_trading_date date;
  snapshot_session_date date;
begin
  if not public.is_valid_pre_event_market_context_v1(input_pre_event_market_context) then
    raise exception 'invalid_pre_event_market_context';
  end if;
  if nullif(btrim(input_market_timezone), '') is null then
    raise exception 'input_market_timezone is required';
  end if;
  if nullif(btrim(input_actor), '') is null then
    raise exception 'input_actor is required';
  end if;

  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;

  begin
    event_trading_date := (existing_row.event_at at time zone input_market_timezone)::date;
    snapshot_session_date := (input_pre_event_market_context ->> 'session_date')::date;
  exception when others then
    raise exception 'invalid_market_timezone_or_session_date';
  end;

  if snapshot_session_date >= event_trading_date then
    raise exception 'pre_event_market_context_not_before_event';
  end if;

  if existing_row.pre_event_market_context is not null then
    if existing_row.pre_event_market_context = input_pre_event_market_context then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_pre_event_context_locked';
  end if;

  if existing_row.status not in ('tracked', 'monitoring') then
    raise exception 'tracked_market_event_not_context_captureable';
  end if;

  update public.tracked_market_events
  set pre_event_market_context = input_pre_event_market_context,
      updated_by = input_actor,
      updated_at = now()
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.capture_tracked_market_event_pre_event_context from public;
grant execute on function public.capture_tracked_market_event_pre_event_context to service_role;

commit;
