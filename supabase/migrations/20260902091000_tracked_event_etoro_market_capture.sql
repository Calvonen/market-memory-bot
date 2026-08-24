-- Capture the eToro-resolved market/exchange label without changing the
-- existing resolution/reference RPC signatures. This keeps rollout backward-
-- compatible with the currently deployed worker while giving later runtime
-- code an immutable, identity-checked write path for resolved_etoro_market.

create or replace function public.guard_tracked_market_event_resolved_market()
returns trigger
language plpgsql
security invoker
as $$
begin
  if tg_op = 'INSERT' then
    if new.resolved_etoro_market is not null then
      raise exception 'tracked_market_event_resolved_market_direct_write_forbidden';
    end if;
    return new;
  end if;

  if new.resolved_etoro_market is not distinct from old.resolved_etoro_market then
    return new;
  end if;

  if old.resolved_etoro_market is not null then
    raise exception 'tracked_market_event_resolved_market_immutable';
  end if;

  -- First capture is permitted only for the exact event/value authorized by
  -- the dedicated RPC below. The transaction-local settings are absent for a
  -- normal PostgREST/service-role table UPDATE, so direct writes fail closed.
  if current_setting('marketai.resolved_etoro_market_capture_event_id', true)
       is distinct from new.id::text
     or current_setting('marketai.resolved_etoro_market_capture_value', true)
       is distinct from new.resolved_etoro_market then
    raise exception 'tracked_market_event_resolved_market_direct_write_forbidden';
  end if;

  if new.resolved_etoro_market is null
     or btrim(new.resolved_etoro_market) = ''
     or new.status <> 'tracked'
     or new.reference_price is not null
     or new.resolved_etoro_instrument_id is null
     or new.resolved_etoro_symbol is null
     or new.resolved_etoro_display_name is null
     or new.resolution_armed_at is null then
    raise exception 'tracked_market_event_resolved_market_invalid_capture';
  end if;

  return new;
end;
$$;

drop trigger if exists guard_tracked_market_event_resolved_market
  on public.tracked_market_events;

create trigger guard_tracked_market_event_resolved_market
before insert or update of resolved_etoro_market
on public.tracked_market_events
for each row
execute function public.guard_tracked_market_event_resolved_market();

create or replace function public.capture_tracked_market_event_resolved_market(
  input_event_id uuid,
  input_etoro_instrument_id bigint,
  input_etoro_symbol text,
  input_etoro_display_name text,
  input_etoro_market text,
  input_actor text
)
returns public.tracked_market_events
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  existing_row public.tracked_market_events%rowtype;
  saved_row public.tracked_market_events%rowtype;
begin
  if input_etoro_instrument_id is null or input_etoro_instrument_id <= 0
     or btrim(coalesce(input_etoro_symbol, '')) = ''
     or btrim(coalesce(input_etoro_display_name, '')) = ''
     or btrim(coalesce(input_etoro_market, '')) = ''
     or btrim(coalesce(input_actor, '')) = '' then
    raise exception 'invalid_tracked_market_event_resolved_market';
  end if;

  select * into existing_row
  from public.tracked_market_events
  where id = input_event_id
  for update;

  if existing_row.id is null then
    raise exception 'tracked_market_event_not_found' using errcode = 'P0002';
  end if;

  if existing_row.resolved_etoro_instrument_id is null
     or existing_row.resolution_armed_at is null then
    raise exception 'tracked_market_event_resolution_missing';
  end if;

  if existing_row.resolved_etoro_instrument_id <> input_etoro_instrument_id
     or upper(existing_row.resolved_etoro_symbol) <> upper(input_etoro_symbol)
     or upper(existing_row.resolved_etoro_display_name) <> upper(input_etoro_display_name) then
    raise exception 'tracked_market_event_resolution_conflict';
  end if;

  -- Exact retries remain idempotent even if the event has since advanced or
  -- captured its reference. A different market is always a conflict.
  if existing_row.resolved_etoro_market is not null then
    if existing_row.resolved_etoro_market = input_etoro_market then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_resolved_market_conflict';
  end if;

  if existing_row.status <> 'tracked' or existing_row.reference_price is not null then
    raise exception 'tracked_market_event_resolved_market_locked';
  end if;

  -- Authorize exactly one first-capture event/value inside this transaction.
  -- These transaction-local settings are not present on ordinary table writes.
  perform pg_catalog.set_config(
    'marketai.resolved_etoro_market_capture_event_id',
    existing_row.id::text,
    true
  );
  perform pg_catalog.set_config(
    'marketai.resolved_etoro_market_capture_value',
    input_etoro_market,
    true
  );

  update public.tracked_market_events
  set resolved_etoro_market = input_etoro_market,
      updated_by = input_actor,
      updated_at = now(),
      last_error = null
  where id = input_event_id
  returning * into saved_row;

  return saved_row;
end;
$$;

revoke all on function public.guard_tracked_market_event_resolved_market() from public;
revoke all on function public.capture_tracked_market_event_resolved_market(uuid,bigint,text,text,text,text) from public;
grant execute on function public.capture_tracked_market_event_resolved_market(uuid,bigint,text,text,text,text) to service_role;
