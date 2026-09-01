-- Canonical deactivation boundary for a tracked instrument.
--
-- Deactivation preserves the tracked-instrument row, sources, profiles and
-- historical event/trading records. It only marks the canonical instrument
-- inactive so discovery can reactivate it later through upsert_tracked_instrument.

create or replace function public.deactivate_tracked_instrument(
  input_tracked_instrument_id text,
  input_actor text
) returns public.tracked_instruments
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  normalized_id text;
  normalized_actor text;
  saved public.tracked_instruments%rowtype;
begin
  normalized_id := btrim(coalesce(input_tracked_instrument_id, ''));
  normalized_actor := btrim(coalesce(input_actor, ''));

  if normalized_id = '' then
    raise exception 'tracked_instrument_invalid_id';
  end if;
  if normalized_actor = '' or length(normalized_actor) > 200 then
    raise exception 'tracked_instrument_invalid_actor';
  end if;

  update public.tracked_instruments
  set
    active = false,
    updated_by = normalized_actor,
    updated_at = now()
  where id = normalized_id
  returning * into saved;

  if saved.id is null then
    raise exception 'tracked_instrument_not_found';
  end if;

  return saved;
end;
$$;

revoke all on function public.deactivate_tracked_instrument(text, text)
  from public, anon, authenticated;
grant execute on function public.deactivate_tracked_instrument(text, text)
  to service_role;
