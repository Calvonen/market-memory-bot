begin;

create table if not exists public.event_official_release_sources (
  event_id text primary key references public.market_events(event_id) on delete cascade,
  source_kind text not null,
  source_url text not null,
  source_title text,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint event_official_release_sources_event_id_nonempty
    check (length(btrim(event_id)) > 0),
  constraint event_official_release_sources_kind_check
    check (source_kind in ('direct_url', 'results_page')),
  constraint event_official_release_sources_url_https
    check (source_url ~ '^https://[^[:space:]]+$'),
  constraint event_official_release_sources_version_positive
    check (version > 0)
);

comment on table public.event_official_release_sources is
  'User-approved official release source for a canonical MarketAI event. Discovery/polling is implemented separately.';
comment on column public.event_official_release_sources.event_id is
  'Canonical event identity backed by market_events(event_id), e.g. calendar:<calendar-event-id>.';
comment on column public.event_official_release_sources.source_kind is
  'direct_url for a known release document; results_page for an approved official results/IR page.';
comment on column public.event_official_release_sources.version is
  'Monotonic compare-and-swap version for approved source changes.';

alter table public.event_official_release_sources enable row level security;

revoke all on table public.event_official_release_sources from anon, authenticated;
grant select on table public.event_official_release_sources to service_role;

create function public.set_event_official_release_source(
  input_event_id text,
  input_source_kind text,
  input_source_url text,
  input_source_title text,
  input_expected_version integer
)
returns table (
  out_event_id text,
  out_source_kind text,
  out_source_url text,
  out_source_title text,
  out_version integer,
  out_created_at timestamptz,
  out_updated_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  current_row public.event_official_release_sources%rowtype;
  write_time timestamptz := clock_timestamp();
begin
  if input_expected_version is null or input_expected_version < 0 then
    raise exception 'invalid_expected_version' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 2));

  if not exists (
    select 1 from public.market_events where event_id = input_event_id
  ) then
    raise exception 'event_not_found: %', input_event_id using errcode = 'P0002';
  end if;

  select * into current_row
  from public.event_official_release_sources
  where event_id = input_event_id;

  if not found then
    if input_expected_version <> 0 then
      raise exception 'version_conflict: expected %, current 0', input_expected_version using errcode = '40001';
    end if;

    insert into public.event_official_release_sources (
      event_id, source_kind, source_url, source_title, version, created_at, updated_at
    ) values (
      input_event_id, input_source_kind, input_source_url, input_source_title, 1, write_time, write_time
    )
    returning * into current_row;
  else
    if current_row.version <> input_expected_version then
      raise exception 'version_conflict: expected %, current %', input_expected_version, current_row.version using errcode = '40001';
    end if;

    update public.event_official_release_sources
    set source_kind = input_source_kind,
        source_url = input_source_url,
        source_title = input_source_title,
        version = current_row.version + 1,
        updated_at = write_time
    where event_id = input_event_id
    returning * into current_row;
  end if;

  return query select
    current_row.event_id,
    current_row.source_kind,
    current_row.source_url,
    current_row.source_title,
    current_row.version,
    current_row.created_at,
    current_row.updated_at;
end;
$$;

revoke all on function public.set_event_official_release_source(text, text, text, text, integer) from public;
grant execute on function public.set_event_official_release_source(text, text, text, text, integer) to service_role;

create function public.clear_event_official_release_source(
  input_event_id text,
  input_expected_version integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  current_version integer;
begin
  if input_expected_version is null or input_expected_version < 1 then
    raise exception 'invalid_expected_version' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 2));

  select version into current_version
  from public.event_official_release_sources
  where event_id = input_event_id;

  if not found then
    raise exception 'version_conflict: expected %, current 0', input_expected_version using errcode = '40001';
  end if;

  if current_version <> input_expected_version then
    raise exception 'version_conflict: expected %, current %', input_expected_version, current_version using errcode = '40001';
  end if;

  delete from public.event_official_release_sources
  where event_id = input_event_id;

  return true;
end;
$$;

revoke all on function public.clear_event_official_release_source(text, integer) from public;
grant execute on function public.clear_event_official_release_source(text, integer) to service_role;

commit;
