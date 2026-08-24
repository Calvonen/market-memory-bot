-- Capture the eToro-resolved market/exchange label without changing the
-- existing resolution/reference RPC signatures. This keeps rollout backward-
-- compatible with the currently deployed worker while giving later runtime
-- code an immutable, identity-checked write path for resolved_etoro_market.

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
security invoker
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

  if existing_row.status <> 'tracked' or existing_row.reference_price is not null then
    raise exception 'tracked_market_event_resolved_market_locked';
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

  if existing_row.resolved_etoro_market is not null then
    if existing_row.resolved_etoro_market = input_etoro_market then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_resolved_market_conflict';
  end if;

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

revoke all on function public.capture_tracked_market_event_resolved_market(uuid,bigint,text,text,text,text) from public;
grant execute on function public.capture_tracked_market_event_resolved_market(uuid,bigint,text,text,text,text) to service_role;
