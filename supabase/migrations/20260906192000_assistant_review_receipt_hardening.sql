-- Bind assistant receipts and strategy identity to the immutable reviewed snapshot.
-- This also promotes durable assistant lineage to the deploy-readiness contract.
-- No LIVE path is added; assistant materialization remains demo-only.

begin;

create or replace function public.assistant_normalize_symbol(input_value text)
returns text
language sql
immutable
strict
set search_path = pg_catalog
as $$
  select upper(regexp_replace(input_value, '[[:space:]]+', '', 'g'));
$$;

revoke all on function public.assistant_normalize_symbol(text)
  from public, anon, authenticated;
grant execute on function public.assistant_normalize_symbol(text) to service_role;

-- Canonicalize proposal symbols before they become reviewable. This mirrors the
-- Python _symbol() identity rule (remove all whitespace, uppercase) and keeps
-- the immutable review snapshot in the same canonical form consumed by SQL.
create or replace function public.normalize_assistant_proposal_symbols()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  normalized_top text;
  normalized_strategy text;
begin
  if new.status not in ('draft', 'ready_for_review') then
    return new;
  end if;

  normalized_top := public.assistant_normalize_symbol(new.payload ->> 'instrument');
  normalized_strategy := public.assistant_normalize_symbol(new.payload #>> '{strategy,instrument}');

  if nullif(normalized_top, '') is null or nullif(normalized_strategy, '') is null then
    raise exception 'assistant_proposal_instrument_missing' using errcode = '22023';
  end if;
  if normalized_top is distinct from normalized_strategy then
    raise exception 'assistant_proposal_strategy_instrument_conflict' using errcode = '55000';
  end if;

  new.payload := jsonb_set(new.payload, '{instrument}', to_jsonb(normalized_top), false);
  new.payload := jsonb_set(new.payload, '{strategy,instrument}', to_jsonb(normalized_strategy), false);
  return new;
end;
$$;

revoke all on function public.normalize_assistant_proposal_symbols()
  from public, anon, authenticated;

drop trigger if exists aa_normalize_assistant_proposal_symbols
  on public.assistant_event_proposals;
create trigger aa_normalize_assistant_proposal_symbols
before insert or update on public.assistant_event_proposals
for each row execute function public.normalize_assistant_proposal_symbols();

-- Existing immutable approved snapshots cannot be silently rewritten. Refuse a
-- deployment if one contains a symbol that does not already satisfy the new
-- canonical rule; it must be explicitly reopened/reviewed instead.
do $$
declare
  bad_count integer;
begin
  select count(*) into bad_count
  from public.assistant_event_proposals p
  where p.status in ('approved_for_materialization', 'materialized')
    and (
      public.assistant_normalize_symbol(p.payload ->> 'instrument')
        is distinct from (p.payload ->> 'instrument')
      or public.assistant_normalize_symbol(p.payload #>> '{strategy,instrument}')
        is distinct from (p.payload #>> '{strategy,instrument}')
    );

  if bad_count > 0 then
    raise exception 'assistant_approved_proposals_require_symbol_rereview: % row(s)', bad_count
      using errcode = '55000';
  end if;
end;
$$;

-- The receipt is the authority evidence later trusted by PAPER approval. Guard
-- that write independently of the application/finalizer: every assistant receipt
-- must match the durable proposal->event binding, immutable review snapshot,
-- canonical market-event identity, and the exact expectation row just written.
create or replace function public.guard_assistant_proposal_receipt_snapshot()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  proposal_id_text text;
  proposal_id_value uuid;
  proposal_row public.assistant_event_proposals%rowtype;
  review_row public.assistant_event_proposal_reviews%rowtype;
  binding public.assistant_proposal_materializations%rowtype;
  market_row public.market_events%rowtype;
  expectation_row public.event_expectation_versions%rowtype;
  strategy jsonb;
  expected_fingerprint text;
begin
  if new.approved_via not like 'assistant_proposal:%' then
    return new;
  end if;

  proposal_id_text := substring(new.approved_via from length('assistant_proposal:') + 1);
  begin
    proposal_id_value := proposal_id_text::uuid;
  exception when others then
    raise exception 'assistant_proposal_receipt_invalid_proposal_id' using errcode = '22023';
  end;

  select * into proposal_row
  from public.assistant_event_proposals
  where id = proposal_id_value
  for update;

  if not found or proposal_row.status <> 'approved_for_materialization' then
    raise exception 'assistant_proposal_receipt_proposal_not_approved' using errcode = '55000';
  end if;
  if proposal_row.requested_execution_mode <> 'demo' then
    raise exception 'assistant_proposal_live_locked' using errcode = '55000';
  end if;

  select * into review_row
  from public.assistant_event_proposal_reviews
  where proposal_id = proposal_id_value
    and review_round = proposal_row.review_round
    and decision = 'approved_for_materialization';

  if not found
     or review_row.payload is distinct from proposal_row.payload
     or review_row.proposal_key is distinct from proposal_row.proposal_key
     or review_row.requested_execution_mode is distinct from 'demo'
     or review_row.reviewed_by is distinct from proposal_row.reviewed_by then
    raise exception 'assistant_proposal_receipt_review_snapshot_conflict' using errcode = '55000';
  end if;

  select * into binding
  from public.assistant_proposal_materializations
  where proposal_id = proposal_id_value;

  if not found
     or binding.review_round is distinct from proposal_row.review_round
     or binding.event_id is distinct from new.event_id then
    raise exception 'assistant_proposal_receipt_materialization_lineage_conflict' using errcode = '55000';
  end if;

  select * into market_row
  from public.market_events
  where event_id = binding.event_id;

  if not found then
    raise exception 'assistant_proposal_receipt_market_event_missing' using errcode = 'P0002';
  end if;

  strategy := review_row.payload -> 'strategy';
  if jsonb_typeof(strategy) is distinct from 'object' then
    raise exception 'assistant_proposal_receipt_strategy_missing' using errcode = '55000';
  end if;

  -- Nested strategy identity is part of reviewed intent, not merely metadata.
  if public.assistant_normalize_symbol(strategy ->> 'instrument')
       is distinct from public.assistant_normalize_symbol(market_row.instrument)
     or nullif(btrim(strategy ->> 'event_name'), '')
       is distinct from nullif(btrim(market_row.event_name), '')
     or nullif(strategy ->> 'scheduled_date', '')::date
       is distinct from market_row.scheduled_date then
    raise exception 'assistant_proposal_receipt_strategy_identity_conflict' using errcode = '55000';
  end if;

  if public.assistant_normalize_symbol(review_row.payload ->> 'instrument')
       is distinct from public.assistant_normalize_symbol(market_row.instrument)
     or (review_row.payload ->> 'scheduled_date')::date
       is distinct from market_row.scheduled_date then
    raise exception 'assistant_proposal_receipt_top_level_identity_conflict' using errcode = '55000';
  end if;

  if new.base_expectation_version
       is distinct from (review_row.payload ->> 'base_expectation_version')::integer
     or new.expectation_version is distinct from new.base_expectation_version + 1
     or new.approved_by is distinct from review_row.reviewed_by
     or btrim(new.change_note) is distinct from btrim(strategy ->> 'change_note') then
    raise exception 'assistant_proposal_receipt_review_metadata_conflict' using errcode = '55000';
  end if;

  select * into expectation_row
  from public.event_expectation_versions
  where event_id = new.event_id
    and version = new.expectation_version;

  if not found then
    raise exception 'assistant_proposal_receipt_expectation_missing' using errcode = 'P0002';
  end if;

  -- Re-derive every persisted strategy field from the immutable review snapshot.
  -- A caller-selected receipt cannot certify a different expectation row.
  if nullif(btrim(expectation_row.source_name), '')
       is distinct from nullif(btrim(strategy ->> 'source_name'), '')
     or nullif(btrim(expectation_row.source_url), '')
       is distinct from nullif(btrim(strategy ->> 'source_url'), '')
     or expectation_row.source_as_of is distinct from nullif(strategy ->> 'source_as_of', '')::date
     or expectation_row.consensus is distinct from coalesce(strategy -> 'consensus', '{}'::jsonb)
     or expectation_row.important_kpis is distinct from
       public.assistant_normalize_text_array(coalesce(strategy -> 'important_kpis', '[]'::jsonb))
     or expectation_row.bull_case is distinct from
       public.assistant_normalize_text_array(coalesce(strategy -> 'bull_case', '[]'::jsonb))
     or expectation_row.base_case is distinct from
       public.assistant_normalize_text_array(coalesce(strategy -> 'base_case', '[]'::jsonb))
     or expectation_row.bear_case is distinct from
       public.assistant_normalize_text_array(coalesce(strategy -> 'bear_case', '[]'::jsonb))
     or expectation_row.triggers is distinct from coalesce(strategy -> 'triggers', '{}'::jsonb)
     or expectation_row.invalidation_conditions is distinct from
       public.assistant_normalize_text_array(
         coalesce(strategy -> 'invalidation_conditions', '[]'::jsonb)
       )
     or btrim(expectation_row.change_note) is distinct from btrim(strategy ->> 'change_note') then
    raise exception 'assistant_proposal_receipt_expectation_snapshot_conflict' using errcode = '55000';
  end if;

  -- Never trust input_draft_fingerprint for assistant receipts. The DB derives
  -- the receipt fingerprint from the immutable reviewed payload + exact durable
  -- event/review lineage. jsonb::text has deterministic key ordering in Postgres.
  expected_fingerprint := encode(
    sha256(
      convert_to(
        jsonb_build_object(
          'event_id', binding.event_id,
          'review_round', binding.review_round,
          'payload', review_row.payload
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );
  new.draft_fingerprint := expected_fingerprint;

  return new;
end;
$$;

revoke all on function public.guard_assistant_proposal_receipt_snapshot()
  from public, anon, authenticated;

drop trigger if exists assistant_proposal_receipt_snapshot_guard
  on public.event_strategy_approvals;
create trigger assistant_proposal_receipt_snapshot_guard
before insert on public.event_strategy_approvals
for each row execute function public.guard_assistant_proposal_receipt_snapshot();

-- Promote the strategy/assistant contract. A backend built for durable assistant
-- lineage must refuse to start against the previous v4 DB even when older
-- strategy/calendar checks still happen to be green.
create or replace function public.strategy_draft_schema_version()
returns integer
language sql
immutable
security invoker
set search_path = public
as $$
  select 5;
$$;

revoke all on function public.strategy_draft_schema_version() from public;
grant execute on function public.strategy_draft_schema_version() to service_role;

drop function if exists public.verify_strategy_draft_schema();

create function public.verify_strategy_draft_schema()
returns table (
  event_strategy_approvals_table_exists boolean,
  approve_strategy_draft_function_exists boolean,
  insert_next_expectation_version_function_exists boolean,
  schema_version_matches boolean,
  calendar_events_table_exists boolean,
  upsert_calendar_candidate_function_exists boolean,
  transition_calendar_event_status_function_exists boolean,
  calendar_candidate_upsert_version_matches boolean,
  calendar_candidate_upsert_implementation_version integer,
  assistant_materialization_lineage_table_exists boolean,
  assistant_materialization_lineage_acl_locked boolean,
  assistant_lineage_enforcement_exists boolean
)
language sql
security invoker
set search_path = public
as $$
  select
    to_regclass('public.event_strategy_approvals') is not null
      and not has_table_privilege(
        'service_role', 'public.event_strategy_approvals', 'INSERT'
      ),
    to_regprocedure(
      'public.approve_strategy_draft(text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text)'
    ) is not null
      and to_regprocedure(
        'public.approve_assistant_proposal_strategy_draft(uuid, integer, text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text, text, text, text, integer, boolean, text)'
      ) is not null,
    to_regprocedure(
      'public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)'
    ) is not null,
    public.strategy_draft_schema_version() = 5,
    to_regclass('public.calendar_events') is not null,
    to_regprocedure(
      'public.upsert_calendar_candidate(text, text, text, text, text, date, text)'
    ) is not null,
    to_regprocedure(
      'public.transition_calendar_event_status(uuid, text, text)'
    ) is not null,
    public.calendar_candidate_upsert_version() = 3,
    public.calendar_candidate_upsert_version(),
    to_regclass('public.assistant_proposal_materializations') is not null,
    not has_table_privilege(
      'service_role', 'public.assistant_proposal_materializations', 'INSERT'
    )
      and not has_table_privilege(
        'service_role', 'public.assistant_proposal_materializations', 'UPDATE'
      )
      and not has_table_privilege(
        'service_role', 'public.assistant_proposal_materializations', 'DELETE'
      ),
    to_regprocedure('public.assistant_normalize_symbol(text)') is not null
      and to_regprocedure('public.guard_assistant_proposal_receipt_snapshot()') is not null
      and exists (
        select 1
        from pg_trigger
        where tgrelid = 'public.event_strategy_approvals'::regclass
          and tgname = 'assistant_proposal_receipt_snapshot_guard'
          and not tgisinternal
      )
      and to_regprocedure(
        'public.upsert_assistant_proposal_canonical_tracked_event(uuid, integer, integer, text, text, text, text, text, text, text, timestamptz, date, text, text, text)'
      ) is not null;
$$;

revoke all on function public.verify_strategy_draft_schema()
  from public, anon, authenticated;
grant execute on function public.verify_strategy_draft_schema()
  to service_role;

commit;
