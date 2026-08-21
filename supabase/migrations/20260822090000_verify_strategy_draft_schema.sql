-- SUPERSEDED: verify_strategy_draft_schema()'s body below is not what is
-- actually deployed once 20260823090000_expectation_write_atomic_response_
-- and_schema_version.sql has been applied - that migration `create or
-- replace`s this same 0-argument function to also report
-- schema_version_matches (whether strategy_draft_schema_version() equals
-- the exact value that migration expects). Kept here, unedited, as the
-- historical record of the gate's original three checks; see that later
-- migration for what actually runs today, and for why a fourth check was
-- needed on top of "does insert_next_expectation_version() exist under
-- this signature."
--
-- Pre-deploy schema gate for the strategy-draft approval flow.
--
-- The backend restart-on-deploy step (.github/workflows/deploy-seesam-hub.yml)
-- has no Postgres-DDL-capable credential and applies no migrations itself -
-- migrations here are applied out-of-band, before merging, through a
-- separate secure mechanism (the Supabase CLI/dashboard). Without a check,
-- a backend commit that calls approve_strategy_draft() or
-- insert_next_expectation_version() could be restarted into production
-- before those migrations have actually been applied, since nothing
-- previously verified that.
--
-- This function is the read-only object that check verifies: safe to call
-- repeatedly, takes no lock, and only checks catalog existence via
-- to_regclass()/to_regprocedure() - no data is read or written. If this
-- migration itself hasn't been applied yet, calling it fails outright
-- (PostgREST "could not find function"), which the deploy gate treats
-- exactly like a false result: fail closed either way.
create or replace function public.verify_strategy_draft_schema()
returns table (
  event_strategy_approvals_table_exists boolean,
  approve_strategy_draft_function_exists boolean,
  insert_next_expectation_version_function_exists boolean
)
language sql
security invoker
set search_path = public
as $$
  select
    to_regclass('public.event_strategy_approvals') is not null,
    to_regprocedure(
      'public.approve_strategy_draft(text, integer, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text, text, text, text)'
    ) is not null,
    to_regprocedure(
      'public.insert_next_expectation_version(text, text, text, date, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, text)'
    ) is not null;
$$;

revoke all on function public.verify_strategy_draft_schema() from public;
grant execute on function public.verify_strategy_draft_schema() to service_role;
