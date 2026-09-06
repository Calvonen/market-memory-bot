-- Safe ingress for assistant-prepared MarketAI event setups.
--
-- This restores the conversational workflow (assistant prepares the target and
-- strategy, user reviews it in the app and separately approves PAPER) without
-- giving the assistant any execution authority. The request row can only be
-- materialized through existing canonical DB boundaries. This migration never
-- creates or approves a trading_task and never calls Strategy/Risk/Broker code.

create table if not exists public.assistant_event_setup_requests (
  id uuid primary key default gen_random_uuid(),
  request_key text not null unique,
  company_name text not null,
  instrument text not null,
  market text not null,
  kind text not null default 'earnings',
  title text not null,
  scheduled_date date not null,
  event_at timestamptz not null,
  event_time_status text not null check (event_time_status in ('confirmed', 'estimated')),
  source_kind text not null check (source_kind in ('direct_url', 'results_page')),
  source_url text not null,
  source_title text,
  strategy_payload jsonb not null default '{}'::jsonb,
  change_note text not null,
  status text not null default 'pending' check (status in ('pending', 'completed')),
  tracked_event_id uuid references public.tracked_market_events(id) on delete set null,
  release_event_id text,
  expectation_version integer,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (jsonb_typeof(strategy_payload) = 'object')
);

alter table public.assistant_event_setup_requests enable row level security;
revoke all on table public.assistant_event_setup_requests from anon, authenticated;
grant select, insert, update on table public.assistant_event_setup_requests to service_role;

create index if not exists assistant_event_setup_requests_status_idx
  on public.assistant_event_setup_requests(status, created_at);

create or replace function public.apply_assistant_event_setup_request(
  input_request_id uuid,
  input_actor text
)
returns table (
  out_request_id uuid,
  out_status text,
  out_tracked_event_id uuid,
  out_release_event_id text,
  out_expectation_version integer
)
language plpgsql
security invoker
set search_path = public
as $$
declare
  req public.assistant_event_setup_requests%rowtype;
  tracked_id uuid;
  release_id text;
  blocker_code text;
  existing_source_kind text;
  existing_source_url text;
  existing_source_title text;
  existing_source_active boolean;
  existing_source_version integer;
  new_expectation_version integer;
  source_as_of date;
begin
  if input_request_id is null then
    raise exception 'assistant_setup_request_id_required' using errcode = '22023';
  end if;
  if input_actor is null or btrim(input_actor) = '' or length(btrim(input_actor)) > 200 then
    raise exception 'assistant_setup_actor_invalid' using errcode = '22023';
  end if;

  -- Serialize one request so retries are idempotent and a duplicate caller can
  -- never create another expectation version after the first one completes.
  perform pg_advisory_xact_lock(hashtextextended(input_request_id::text, 7));

  select * into req
  from public.assistant_event_setup_requests
  where id = input_request_id
  for update;

  if not found then
    raise exception 'assistant_setup_request_not_found: %', input_request_id using errcode = 'P0002';
  end if;

  if req.status = 'completed' then
    return query select
      req.id,
      req.status,
      req.tracked_event_id,
      req.release_event_id,
      req.expectation_version;
    return;
  end if;

  if btrim(req.request_key) = ''
     or btrim(req.company_name) = ''
     or btrim(req.instrument) = ''
     or btrim(req.market) = ''
     or btrim(req.kind) = ''
     or btrim(req.title) = ''
     or btrim(req.change_note) = '' then
    raise exception 'assistant_setup_required_text_missing' using errcode = '22023';
  end if;

  -- A generic tracked event requires an explicit timezone-aware runtime anchor.
  -- Keep the date identity fail-closed: callers must choose an event_at whose
  -- UTC date is the same occurrence date instead of silently shifting the
  -- release shell onto an adjacent day.
  if (req.event_at at time zone 'UTC')::date <> req.scheduled_date then
    raise exception 'assistant_setup_event_at_date_mismatch' using errcode = '22023';
  end if;

  if req.kind <> 'earnings' then
    raise exception 'assistant_setup_kind_not_supported: %', req.kind using errcode = '22023';
  end if;

  if req.strategy_payload ? 'consensus'
     and jsonb_typeof(req.strategy_payload->'consensus') <> 'object' then
    raise exception 'assistant_setup_consensus_must_be_object' using errcode = '22023';
  end if;
  if req.strategy_payload ? 'triggers'
     and jsonb_typeof(req.strategy_payload->'triggers') <> 'object' then
    raise exception 'assistant_setup_triggers_must_be_object' using errcode = '22023';
  end if;
  if req.strategy_payload ? 'important_kpis'
     and jsonb_typeof(req.strategy_payload->'important_kpis') <> 'array' then
    raise exception 'assistant_setup_important_kpis_must_be_array' using errcode = '22023';
  end if;
  if req.strategy_payload ? 'bull_case'
     and jsonb_typeof(req.strategy_payload->'bull_case') <> 'array' then
    raise exception 'assistant_setup_bull_case_must_be_array' using errcode = '22023';
  end if;
  if req.strategy_payload ? 'base_case'
     and jsonb_typeof(req.strategy_payload->'base_case') <> 'array' then
    raise exception 'assistant_setup_base_case_must_be_array' using errcode = '22023';
  end if;
  if req.strategy_payload ? 'bear_case'
     and jsonb_typeof(req.strategy_payload->'bear_case') <> 'array' then
    raise exception 'assistant_setup_bear_case_must_be_array' using errcode = '22023';
  end if;
  if req.strategy_payload ? 'invalidation_conditions'
     and jsonb_typeof(req.strategy_payload->'invalidation_conditions') <> 'array' then
    raise exception 'assistant_setup_invalidation_conditions_must_be_array' using errcode = '22023';
  end if;

  -- Canonical generic tracked-event upsert. request_key is the durable
  -- idempotency identity: rerunning the same setup resolves to the same tracked
  -- row rather than creating a second event.
  select u.out_id
    into tracked_id
  from public.upsert_tracked_market_event(
    req.company_name,
    upper(replace(req.instrument, ' ', '')),
    req.market,
    'assistant_setup',
    'assistant:' || req.request_key,
    req.kind,
    req.title,
    req.event_at,
    req.event_time_status,
    btrim(input_actor),
    null
  ) as u;

  if tracked_id is null then
    raise exception 'assistant_setup_tracked_event_not_created';
  end if;

  -- Reuse the same release-shell boundary as calendar_release_worker. It
  -- creates/validates market_events + the initial expectation identity and
  -- refuses any conflicting pre-existing shell.
  select s.out_release_event_id, s.out_blocker_code
    into release_id, blocker_code
  from public.ensure_tracked_event_release_shell_with_blocker(tracked_id) as s;

  if release_id is null or release_id = '' then
    raise exception 'assistant_setup_release_shell_missing';
  end if;
  if blocker_code is not null and blocker_code <> '' then
    raise exception 'assistant_setup_release_shell_blocked: %', blocker_code;
  end if;

  -- Official source is canonical and versioned. A retry may reuse an identical
  -- source, but the assistant path will never overwrite a different source that
  -- was already approved elsewhere.
  select
    s.out_source_kind,
    s.out_source_url,
    s.out_source_title,
    s.out_is_active,
    s.out_version
  into
    existing_source_kind,
    existing_source_url,
    existing_source_title,
    existing_source_active,
    existing_source_version
  from public.get_audited_official_release_source_state(release_id) as s;

  if existing_source_active then
    if existing_source_kind is distinct from req.source_kind
       or existing_source_url is distinct from req.source_url
       or existing_source_title is distinct from req.source_title then
      raise exception 'assistant_setup_official_source_conflict';
    end if;
  else
    perform 1
    from public.set_event_official_release_source_approved(
      release_id,
      req.source_kind,
      req.source_url,
      req.source_title,
      existing_source_version,
      btrim(input_actor)
    );
  end if;

  if nullif(req.strategy_payload->>'source_as_of', '') is not null then
    source_as_of := (req.strategy_payload->>'source_as_of')::date;
  end if;

  -- Persist the assistant-authored strategy as a normal immutable expectation
  -- version. The user can inspect this version in the app. This step does NOT
  -- approve PAPER; the separate tracked-event PAPER-permission endpoint still
  -- requires the user's explicit action and pins that exact expectation version.
  select e.out_version
    into new_expectation_version
  from public.insert_next_expectation_version(
    release_id,
    nullif(req.strategy_payload->>'source_name', ''),
    nullif(req.strategy_payload->>'source_url', ''),
    source_as_of,
    req.strategy_payload->'consensus',
    req.strategy_payload->'important_kpis',
    req.strategy_payload->'bull_case',
    req.strategy_payload->'base_case',
    req.strategy_payload->'bear_case',
    req.strategy_payload->'triggers',
    req.strategy_payload->'invalidation_conditions',
    req.change_note
  ) as e;

  if new_expectation_version is null then
    raise exception 'assistant_setup_expectation_not_written';
  end if;

  update public.assistant_event_setup_requests
     set status = 'completed',
         tracked_event_id = tracked_id,
         release_event_id = release_id,
         expectation_version = new_expectation_version,
         completed_at = now(),
         updated_at = now()
   where id = req.id;

  return query select
    req.id,
    'completed'::text,
    tracked_id,
    release_id,
    new_expectation_version;
end;
$$;

revoke all on function public.apply_assistant_event_setup_request(uuid, text) from public;
grant execute on function public.apply_assistant_event_setup_request(uuid, text) to service_role;

comment on table public.assistant_event_setup_requests is
  'Non-execution staging queue for assistant-prepared event setups; PAPER authority is never stored here.';

comment on function public.apply_assistant_event_setup_request(uuid, text) is
  'Atomically materializes one assistant setup via canonical tracked-event, release-source and expectation RPCs. Never grants PAPER execution authority.';
