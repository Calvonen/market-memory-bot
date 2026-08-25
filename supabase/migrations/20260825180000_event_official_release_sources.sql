begin;

create table if not exists public.event_official_release_sources (
  event_id text primary key,
  source_kind text not null,
  source_url text not null,
  source_title text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint event_official_release_sources_event_id_nonempty
    check (length(btrim(event_id)) > 0),
  constraint event_official_release_sources_kind_check
    check (source_kind in ('direct_url', 'results_page')),
  constraint event_official_release_sources_url_https
    check (source_url ~ '^https://[^[:space:]]+$')
);

comment on table public.event_official_release_sources is
  'User-approved official release source for a canonical MarketAI event. Discovery/polling is implemented separately.';
comment on column public.event_official_release_sources.event_id is
  'Canonical event identity, e.g. calendar:<calendar-event-id>.';
comment on column public.event_official_release_sources.source_kind is
  'direct_url for a known release document; results_page for an approved official results/IR page.';

alter table public.event_official_release_sources enable row level security;

revoke all on table public.event_official_release_sources from anon, authenticated;
grant select, insert, update, delete on table public.event_official_release_sources to service_role;

commit;
