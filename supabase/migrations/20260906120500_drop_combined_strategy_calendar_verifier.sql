-- The deployed strategy schema verifier was later widened by calendar migrations
-- from 4 to 9 OUT columns. The assistant migrations intentionally redefine the
-- strategy portion several times, but CREATE OR REPLACE cannot change an OUT
-- row type. Drop the combined verifier before the first assistant redefinition;
-- the final compatibility migration restores the full 9-column contract.

begin;

drop function if exists public.verify_strategy_draft_schema();

commit;
