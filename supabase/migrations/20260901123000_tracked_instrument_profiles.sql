-- Minimal persisted tracking intent for canonical tracked instruments.
--
-- Profiles describe why an instrument is tracked and the user's short specs.
-- They are configuration only: this migration must not create tracked events,
-- strategy/risk decisions, broker actions, trading tasks, or trades.

create table public.tracked_instrument_profiles (
  id text primary key default replace(gen_random_uuid()::text, '-', ''),
  tracked_instrument_id text not null
    references public.tracked_instruments(id) on delete cascade,
  profile_type text not null check (
    profile_type in ('earnings', 'trend', 'future_tech')
  ),
  specs text not null default '' check (
    specs = btrim(specs) and length(specs) <= 4000
  ),
  enabled boolean not null default true,
  created_by text not null check (
    created_by = btrim(created_by) and length(created_by) between 1 and 200
  ),
  updated_by text not null check (
    updated_by = btrim(updated_by) and length(updated_by) between 1 and 200
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tracked_instrument_id, profile_type)
);

comment on table public.tracked_instrument_profiles is
  'User-selected tracking intent and short specs for a canonical tracked instrument. Configuration only; it does not create events or trades.';

alter table public.tracked_instrument_profiles enable row level security;
revoke all on table public.tracked_instrument_profiles
  from public, anon, authenticated, service_role;
grant select on table public.tracked_instrument_profiles to service_role;
