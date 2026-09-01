-- Persistent canonical instrument-tracking registry.
--
-- This is deliberately separate from tracked_market_events: adding an instrument
-- to the registry must not fabricate a market event, expectation, strategy
-- decision, risk decision, broker action, or trading task.

create table public.tracked_instruments (
  id text primary key default replace(gen_random_uuid()::text, '-', ''),
  instrument text not null check (btrim(instrument) <> ''),
  market text not null default '' check (market = btrim(market)),
  company_name text not null default '' check (company_name = btrim(company_name)),
  instrument_key text generated always as (
    upper(regexp_replace(btrim(instrument), '\s+', '', 'g'))
  ) stored,
  market_key text generated always as (
    upper(regexp_replace(btrim(market), '\s+', ' ', 'g'))
  ) stored,
  sources text[] not null check (
    cardinality(sources) > 0
    and sources <@ array['scanner', 'calendar', 'manual']::text[]
    and array_position(sources, null) is null
  ),
  active boolean not null default true,
  created_by text not null check (
    created_by = btrim(created_by) and length(created_by) between 1 and 200
  ),
  updated_by text not null check (
    updated_by = btrim(updated_by) and length(updated_by) between 1 and 200
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (instrument_key, market_key)
);

comment on table public.tracked_instruments is
  'Canonical persistent instrument tracking. Instrument tracking is separate from concrete market events and trading execution.';

alter table public.tracked_instruments enable row level security;
revoke all on table public.tracked_instruments from public, anon, authenticated, service_role;
grant select on table public.tracked_instruments to service_role;

create or replace function public.upsert_tracked_instrument(
  input_instrument text,
  input_company_name text,
  input_market text,
  input_source text,
  input_actor text
) returns public.tracked_instruments
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  normalized_instrument text;
  normalized_company_name text;
  normalized_market text;
  normalized_source text;
  normalized_actor text;
  saved public.tracked_instruments%rowtype;
begin
  normalized_instrument := upper(regexp_replace(btrim(coalesce(input_instrument, '')), '\s+', '', 'g'));
  normalized_company_name := btrim(coalesce(input_company_name, ''));
  normalized_market := regexp_replace(btrim(coalesce(input_market, '')), '\s+', ' ', 'g');
  normalized_source := lower(btrim(coalesce(input_source, '')));
  normalized_actor := btrim(coalesce(input_actor, ''));

  if normalized_instrument = '' then
    raise exception 'tracked_instrument_invalid_instrument';
  end if;
  if normalized_source not in ('scanner', 'calendar', 'manual') then
    raise exception 'tracked_instrument_invalid_source';
  end if;
  if normalized_actor = '' or length(normalized_actor) > 200 then
    raise exception 'tracked_instrument_invalid_actor';
  end if;

  insert into public.tracked_instruments (
    instrument,
    market,
    company_name,
    sources,
    active,
    created_by,
    updated_by
  ) values (
    normalized_instrument,
    normalized_market,
    normalized_company_name,
    array[normalized_source],
    true,
    normalized_actor,
    normalized_actor
  )
  on conflict (instrument_key, market_key) do update
  set
    company_name = case
      when excluded.company_name <> '' then excluded.company_name
      else tracked_instruments.company_name
    end,
    market = excluded.market,
    sources = case
      when excluded.sources[1] = any(tracked_instruments.sources)
        then tracked_instruments.sources
      else tracked_instruments.sources || excluded.sources[1]
    end,
    active = true,
    updated_by = excluded.updated_by,
    updated_at = now()
  returning * into saved;

  return saved;
end;
$$;

revoke all on function public.upsert_tracked_instrument(text, text, text, text, text)
  from public, anon, authenticated;
grant execute on function public.upsert_tracked_instrument(text, text, text, text, text)
  to service_role;

create or replace function public.tracked_instrument_registry_schema_version()
returns integer
language sql
immutable
security invoker
as $$
  select 1;
$$;

revoke all on function public.tracked_instrument_registry_schema_version from public;
grant execute on function public.tracked_instrument_registry_schema_version to service_role;

create or replace function public.verify_tracked_instrument_registry_schema()
returns table (
  tracked_instruments_table_exists boolean,
  upsert_tracked_instrument_function_exists boolean,
  tracked_instrument_registry_schema_version integer
)
language sql
stable
security invoker
as $$
  select
    to_regclass('public.tracked_instruments') is not null,
    to_regprocedure('public.upsert_tracked_instrument(text,text,text,text,text)') is not null,
    public.tracked_instrument_registry_schema_version();
$$;

revoke all on function public.verify_tracked_instrument_registry_schema from public;
grant execute on function public.verify_tracked_instrument_registry_schema to service_role;
