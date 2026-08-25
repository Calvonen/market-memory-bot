begin;

create function public.is_valid_official_release_https_url(input_url text)
returns boolean
language plpgsql
immutable
security invoker
set search_path = public
as $$
declare
  authority text;
  host_port text;
  host_part text;
  trimmed_host text;
  port_text text;
  port_value integer;
  ipv6_text text;
  label text;
begin
  if input_url is null or input_url !~ '^https://[^[:space:]]+$' then
    return false;
  end if;

  authority := regexp_replace(substr(input_url, 9), '[/\?#].*$', '');
  if authority = '' or position('@' in authority) > 0 then
    return false;
  end if;

  host_port := authority;
  if left(host_port, 1) = '[' then
    if position(']' in host_port) = 0 then
      return false;
    end if;
    host_part := split_part(host_port, ']', 1) || ']';
    port_text := substr(host_port, length(host_part) + 1);
    ipv6_text := substr(host_part, 2, length(host_part) - 2);
    if ipv6_text = '' then
      return false;
    end if;
    begin
      if family(ipv6_text::inet) <> 6 or masklen(ipv6_text::inet) <> 128 then
        return false;
      end if;
    exception
      when others then
        return false;
    end;
    if port_text <> '' then
      if left(port_text, 1) <> ':' then
        return false;
      end if;
      port_text := substr(port_text, 2);
    end if;
  else
    if host_port ~ ':[^:]*$' then
      host_part := regexp_replace(host_port, ':[^:]*$', '');
      port_text := regexp_replace(host_port, '^.*:', '');
    else
      host_part := host_port;
      port_text := '';
    end if;
    if host_part = '' then
      return false;
    end if;

    begin
      if family(host_part::inet) = 4 and masklen(host_part::inet) = 32 then
        null;
      else
        return false;
      end if;
    exception
      when others then
        if length(host_part) > 253 then
          return false;
        end if;
        trimmed_host := rtrim(host_part, '.');
        if trimmed_host = '' then
          return false;
        end if;
        foreach label in array string_to_array(trimmed_host, '.') loop
          if label = '' or length(label) > 63 then
            return false;
          end if;
          if left(label, 1) = '-' or right(label, 1) = '-' then
            return false;
          end if;
          if label !~ '^[A-Za-z0-9-]+$' then
            return false;
          end if;
        end loop;
      end;
  end if;

  if port_text <> '' then
    if port_text !~ '^[0-9]{1,5}$' then
      return false;
    end if;
    port_value := port_text::integer;
    if port_value < 1 or port_value > 65535 then
      return false;
    end if;
  elsif right(authority, 1) = ':' then
    return false;
  end if;

  return true;
exception
  when others then
    return false;
end;
$$;

revoke all on function public.is_valid_official_release_https_url(text) from public;
grant execute on function public.is_valid_official_release_https_url(text) to service_role;

create table if not exists public.event_official_release_sources (
  event_id text primary key references public.market_events(event_id) on delete cascade,
  source_kind text,
  source_url text,
  source_title text,
  is_active boolean not null default true,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint event_official_release_sources_event_id_nonempty
    check (length(btrim(event_id)) > 0),
  constraint event_official_release_sources_active_shape_check
    check (
      (
        is_active
        and source_kind is not null
        and source_kind in ('direct_url', 'results_page')
        and source_url is not null
      )
      or
      (not is_active and source_kind is null and source_url is null and source_title is null)
    ),
  constraint event_official_release_sources_url_https
    check (source_url is null or public.is_valid_official_release_https_url(source_url)),
  constraint event_official_release_sources_version_positive
    check (version > 0)
);

comment on table public.event_official_release_sources is
  'User-approved official release source for a canonical MarketAI event. Cleared sources remain as versioned tombstones so CAS versions never reset.';
comment on column public.event_official_release_sources.event_id is
  'Canonical event identity backed by market_events(event_id), e.g. calendar:<calendar-event-id>.';
comment on column public.event_official_release_sources.source_kind is
  'direct_url for a known release document; results_page for an approved official results/IR page; null only for an inactive tombstone.';
comment on column public.event_official_release_sources.is_active is
  'True when an approved source exists; false for a cleared versioned tombstone.';
comment on column public.event_official_release_sources.version is
  'Monotonic compare-and-swap version for approved source changes and clears.';

alter table public.event_official_release_sources enable row level security;

revoke all on table public.event_official_release_sources from anon, authenticated, service_role;
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
  if input_source_kind is null or input_source_kind not in ('direct_url', 'results_page') then
    raise exception 'invalid_source_kind' using errcode = '22023';
  end if;
  if not public.is_valid_official_release_https_url(input_source_url) then
    raise exception 'invalid_source_url' using errcode = '22023';
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
      event_id, source_kind, source_url, source_title, is_active, version, created_at, updated_at
    ) values (
      input_event_id, input_source_kind, input_source_url, input_source_title, true, 1, write_time, write_time
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
        is_active = true,
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
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  current_row public.event_official_release_sources%rowtype;
  write_time timestamptz := clock_timestamp();
begin
  if input_expected_version is null or input_expected_version < 1 then
    raise exception 'invalid_expected_version' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(input_event_id, 2));

  select * into current_row
  from public.event_official_release_sources
  where event_id = input_event_id;

  if not found then
    raise exception 'version_conflict: expected %, current 0', input_expected_version using errcode = '40001';
  end if;

  if current_row.version <> input_expected_version then
    raise exception 'version_conflict: expected %, current %', input_expected_version, current_row.version using errcode = '40001';
  end if;

  update public.event_official_release_sources
  set source_kind = null,
      source_url = null,
      source_title = null,
      is_active = false,
      version = current_row.version + 1,
      updated_at = write_time
  where event_id = input_event_id
  returning version into current_row.version;

  return current_row.version;
end;
$$;

revoke all on function public.clear_event_official_release_source(text, integer) from public;
grant execute on function public.clear_event_official_release_source(text, integer) to service_role;

commit;
