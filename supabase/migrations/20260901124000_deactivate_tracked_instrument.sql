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

-- Registry schema v2 requires canonical deactivation in addition to the v1
-- table/upsert contract. The deploy verifier must fail closed if this migration
-- has not been applied before the backend exposes the deactivate endpoint.
create or replace function public.tracked_instrument_registry_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 2;
$$;

-- The verifier gains one output column in v2. PostgreSQL cannot change a
-- function's TABLE return shape with CREATE OR REPLACE, so replace the verifier
-- explicitly inside this migration transaction.
drop function if exists public.verify_tracked_instrument_registry_schema();

create function public.verify_tracked_instrument_registry_schema()
returns table (
  tracked_instruments_table_exists boolean,
  upsert_tracked_instrument_function_exists boolean,
  deactivate_tracked_instrument_function_exists boolean,
  tracked_instrument_registry_schema_version integer
)
language sql
stable
security invoker
as $$
  select
    to_regclass('public.tracked_instruments') is not null,
    to_regprocedure('public.upsert_tracked_instrument(text,text,text,text,text)') is not null,
    to_regprocedure('public.deactivate_tracked_instrument(text,text)') is not null,
    public.tracked_instrument_registry_schema_version();
$$;

revoke all on function public.verify_tracked_instrument_registry_schema from public;
grant execute on function public.verify_tracked_instrument_registry_schema to service_role;
