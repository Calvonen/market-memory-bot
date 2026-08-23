alter table public.tracked_market_events
  add column if not exists tracking_config_snapshot jsonb;

comment on column public.tracked_market_events.tracking_config_snapshot is
  'Immutable snapshot of the effective reaction-monitoring settings used for this tracked event. Null for legacy/not-yet-started events.';

create or replace function public.capture_tracked_market_event_config_snapshot(
  input_event_id uuid,
  input_tracking_config_snapshot jsonb,
  input_actor text
)
returns setof public.tracked_market_events
language plpgsql
security definer
set search_path = public
as $$
begin
  if input_event_id is null then
    raise exception 'input_event_id is required';
  end if;
  if input_tracking_config_snapshot is null
     or jsonb_typeof(input_tracking_config_snapshot) <> 'object'
     or input_tracking_config_snapshot = '{}'::jsonb then
    raise exception 'input_tracking_config_snapshot must be a non-empty JSON object';
  end if;
  if nullif(btrim(input_actor), '') is null then
    raise exception 'input_actor is required';
  end if;

  return query
  update public.tracked_market_events
     set tracking_config_snapshot = input_tracking_config_snapshot,
         updated_by = input_actor,
         updated_at = now()
   where id = input_event_id
     and tracking_config_snapshot is null
     and status in ('tracked', 'monitoring')
  returning *;

  if not found then
    return query
    select *
      from public.tracked_market_events
     where id = input_event_id
       and tracking_config_snapshot = input_tracking_config_snapshot;
  end if;
end;
$$;

revoke all on function public.capture_tracked_market_event_config_snapshot(uuid, jsonb, text) from public;
grant execute on function public.capture_tracked_market_event_config_snapshot(uuid, jsonb, text) to service_role;
