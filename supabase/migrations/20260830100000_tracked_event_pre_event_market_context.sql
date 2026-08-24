begin;

alter table public.tracked_market_events
  add column if not exists pre_event_market_context jsonb;

comment on column public.tracked_market_events.pre_event_market_context is
  'Immutable versioned snapshot of the last complete trading session before the event. Observation-only; null until captured.';

create or replace function public.is_valid_pre_event_market_context_v1(snapshot jsonb)
returns boolean
language plpgsql
immutable
as $$
declare
  open_price numeric;
  high_price numeric;
  low_price numeric;
  close_price numeric;
  session_return numeric;
  expected_return numeric;
begin
  if snapshot is null or jsonb_typeof(snapshot) <> 'object' then
    return false;
  end if;

  if (select count(*) from jsonb_object_keys(snapshot)) <> 8 then
    return false;
  end if;

  if not (snapshot ? 'schema_version')
     or jsonb_typeof(snapshot -> 'schema_version') <> 'number'
     or (snapshot ->> 'schema_version')::numeric <> 1 then
    return false;
  end if;

  if not (snapshot ? 'session_date')
     or jsonb_typeof(snapshot -> 'session_date') <> 'string' then
    return false;
  end if;
  begin
    perform (snapshot ->> 'session_date')::date;
  exception when others then
    return false;
  end;

  if not (snapshot ? 'open_price')
     or not (snapshot ? 'high_price')
     or not (snapshot ? 'low_price')
     or not (snapshot ? 'close_price')
     or jsonb_typeof(snapshot -> 'open_price') <> 'string'
     or jsonb_typeof(snapshot -> 'high_price') <> 'string'
     or jsonb_typeof(snapshot -> 'low_price') <> 'string'
     or jsonb_typeof(snapshot -> 'close_price') <> 'string' then
    return false;
  end if;

  begin
    open_price := (snapshot ->> 'open_price')::numeric;
    high_price := (snapshot ->> 'high_price')::numeric;
    low_price := (snapshot ->> 'low_price')::numeric;
    close_price := (snapshot ->> 'close_price')::numeric;
  exception when others then
    return false;
  end;

  if open_price <= 0 or high_price <= 0 or low_price <= 0 or close_price <= 0 then
    return false;
  end if;
  if high_price < greatest(open_price, close_price, low_price) then
    return false;
  end if;
  if low_price > least(open_price, close_price, high_price) then
    return false;
  end if;

  if not (snapshot ? 'session_return_pct')
     or jsonb_typeof(snapshot -> 'session_return_pct') <> 'string' then
    return false;
  end if;
  begin
    session_return := (snapshot ->> 'session_return_pct')::numeric;
  exception when others then
    return false;
  end;
  expected_return := ((close_price / open_price) - 1) * 100;
  if abs(expected_return - session_return) > 0.000001 then
    return false;
  end if;

  if not (snapshot ? 'late_session_return_pct') then
    return false;
  end if;
  if snapshot -> 'late_session_return_pct' <> 'null'::jsonb then
    if jsonb_typeof(snapshot -> 'late_session_return_pct') <> 'string' then
      return false;
    end if;
    begin
      perform (snapshot ->> 'late_session_return_pct')::numeric;
    exception when others then
      return false;
    end;
  end if;

  return true;
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

create or replace function public.enforce_tracked_market_event_pre_event_context_immutable()
returns trigger
language plpgsql
as $$
begin
  if OLD.pre_event_market_context is not null
     and NEW.pre_event_market_context is distinct from OLD.pre_event_market_context then
    raise exception 'tracked_market_event_pre_event_context_immutable';
  end if;
  return NEW;
end;
$$;

revoke all on function public.enforce_tracked_market_event_pre_event_context_immutable from public;
grant execute on function public.enforce_tracked_market_event_pre_event_context_immutable to service_role;

drop trigger if exists tracked_market_events_pre_event_context_immutable on public.tracked_market_events;

create trigger tracked_market_events_pre_event_context_immutable
  before update on public.tracked_market_events
  for each row
  execute function public.enforce_tracked_market_event_pre_event_context_immutable();

create or replace function public.capture_tracked_market_event_pre_event_context(
  input_event_id uuid,
  input_pre_event_market_context jsonb,
  input_actor text
)
returns public.tracked_market_events
language plpgsql
security invoker
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
begin
  if not public.is_valid_pre_event_market_context_v1(input_pre_event_market_context) then
    raise exception 'invalid_pre_event_market_context';
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

  if existing_row.pre_event_market_context is not null then
    if existing_row.pre_event_market_context = input_pre_event_market_context then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_pre_event_context_locked';
  end if;

  if existing_row.status not in ('tracked', 'monitoring') then
    raise exception 'tracked_market_event_not_context_captureable';
  end if;

  if (input_pre_event_market_context ->> 'session_date')::date >= existing_row.event_at::date then
    raise exception 'pre_event_session_not_before_event_date';
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
