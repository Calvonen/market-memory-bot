-- Allow a one-way resolved eToro market backfill for legacy events that had
-- already entered MONITORING before resolved_etoro_market capture was deployed.
-- New TRACKED events retain the original pre-reference capture requirement.

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

  if new.resolved_etoro_market is null
     or btrim(new.resolved_etoro_market) = ''
     or new.status not in ('tracked', 'monitoring')
     or (new.status = 'tracked' and new.reference_price is not null)
     or new.resolved_etoro_instrument_id is null
     or new.resolved_etoro_symbol is null
     or new.resolved_etoro_display_name is null
     or new.resolution_armed_at is null then
    raise exception 'tracked_market_event_resolved_market_invalid_capture';
  end if;

  return new;
end;
$$;

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

  if existing_row.resolved_etoro_market is not null then
    if existing_row.resolved_etoro_market = input_etoro_market then
      return existing_row;
    end if;
    raise exception 'tracked_market_event_resolved_market_conflict';
  end if;

  if existing_row.status not in ('tracked', 'monitoring')
     or (existing_row.status = 'tracked' and existing_row.reference_price is not null) then
    raise exception 'tracked_market_event_resolved_market_locked';
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

revoke all on function public.guard_tracked_market_event_resolved_market() from public;
revoke all on function public.capture_tracked_market_event_resolved_market(uuid,bigint,text,text,text,text) from public;
grant execute on function public.capture_tracked_market_event_resolved_market(uuid,bigint,text,text,text,text) to service_role;
