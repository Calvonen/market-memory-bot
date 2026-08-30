-- Canonical mutation boundary for tracked-instrument profiles.
--
-- Profile writes remain configuration-only. This function may mutate only
-- tracked_instrument_profiles; it must never create or update events, strategy,
-- risk, broker, paper/live execution, or other trading state.

create or replace function public.upsert_tracked_instrument_profile(
  input_tracked_instrument_id text,
  input_profile_type text,
  input_specs text,
  input_enabled boolean,
  input_actor text
) returns public.tracked_instrument_profiles
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  normalized_tracked_instrument_id text;
  normalized_profile_type text;
  normalized_specs text;
  normalized_actor text;
  saved public.tracked_instrument_profiles%rowtype;
begin
  normalized_tracked_instrument_id := btrim(coalesce(input_tracked_instrument_id, ''));
  normalized_profile_type := lower(btrim(coalesce(input_profile_type, '')));
  normalized_specs := btrim(coalesce(input_specs, ''));
  normalized_actor := btrim(coalesce(input_actor, ''));

  if normalized_tracked_instrument_id = '' then
    raise exception 'tracked_profile_invalid_instrument_id';
  end if;
  if normalized_profile_type not in ('earnings', 'trend', 'future_tech') then
    raise exception 'tracked_profile_invalid_type';
  end if;
  if length(normalized_specs) > 4000 then
    raise exception 'tracked_profile_specs_too_long';
  end if;
  if normalized_actor = '' or length(normalized_actor) > 200 then
    raise exception 'tracked_profile_invalid_actor';
  end if;
  if input_enabled is null then
    raise exception 'tracked_profile_invalid_enabled';
  end if;
  if not exists (
    select 1
    from public.tracked_instruments
    where id = normalized_tracked_instrument_id
  ) then
    raise exception 'tracked_profile_instrument_not_found';
  end if;

  insert into public.tracked_instrument_profiles (
    tracked_instrument_id,
    profile_type,
    specs,
    enabled,
    created_by,
    updated_by
  ) values (
    normalized_tracked_instrument_id,
    normalized_profile_type,
    normalized_specs,
    input_enabled,
    normalized_actor,
    normalized_actor
  )
  on conflict (tracked_instrument_id, profile_type) do update
  set
    specs = excluded.specs,
    enabled = excluded.enabled,
    updated_by = excluded.updated_by,
    updated_at = now()
  returning * into saved;

  return saved;
end;
$$;

revoke all on function public.upsert_tracked_instrument_profile(text, text, text, boolean, text)
  from public, anon, authenticated;
grant execute on function public.upsert_tracked_instrument_profile(text, text, text, boolean, text)
  to service_role;

create or replace function public.tracked_instrument_profile_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 1;
$$;

revoke all on function public.tracked_instrument_profile_schema_version from public;
grant execute on function public.tracked_instrument_profile_schema_version to service_role;

create or replace function public.verify_tracked_instrument_profile_schema()
returns table (
  tracked_instrument_profiles_table_exists boolean,
  upsert_tracked_instrument_profile_function_exists boolean,
  tracked_instrument_profile_schema_version integer
)
language sql
stable
security invoker
as $$
  select
    to_regclass('public.tracked_instrument_profiles') is not null,
    to_regprocedure('public.upsert_tracked_instrument_profile(text,text,text,boolean,text)') is not null,
    public.tracked_instrument_profile_schema_version();
$$;

revoke all on function public.verify_tracked_instrument_profile_schema from public;
grant execute on function public.verify_tracked_instrument_profile_schema to service_role;
