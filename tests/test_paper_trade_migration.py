import unittest
from datetime import UTC, datetime
from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20260820100000_atomic_paper_run_transitions.sql"
)


def _first_terminal(rows: list[dict[str, str]]) -> dict[str, str]:
    """Model the migration's timestamp/ID ordering for regression fixtures."""
    return min(
        rows,
        key=lambda row: (
            datetime.fromisoformat(row["terminal_transition_at"]).astimezone(UTC),
            row["first_seen_at"],
            row["id"],
        ),
    )


class PaperTradeMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_terminal_event_has_partial_unique_index(self) -> None:
        self.assertIn("create unique index", self.sql)
        self.assertIn("on public.event_paper_trade_runs(event_id)", self.sql)
        self.assertIn("where status in ('expired_no_trade', 'paper_executed')", self.sql)

    def test_existing_terminal_duplicates_are_reconciled_before_unique_index(self) -> None:
        reconcile_at = self.sql.index("with ranked_terminal as")
        index_at = self.sql.index("create unique index")
        self.assertLess(reconcile_at, index_at)
        self.assertIn("row_number() over", self.sql)
        self.assertIn("status = 'superseded'", self.sql)
        self.assertIn("superseded_by_analysis_id", self.sql)

    def test_both_transition_functions_take_event_scoped_transaction_lock(self) -> None:
        self.assertEqual(self.sql.count("pg_advisory_xact_lock"), 3)
        self.assertIn("hashtextextended(effective_event_id, 0)", self.sql)
        self.assertIn("hashtextextended(input_event_id, 0)", self.sql)

    def test_both_transition_functions_return_existing_terminal_owner(self) -> None:
        owner_predicate = "status in ('expired_no_trade', 'paper_executed')"
        self.assertGreaterEqual(self.sql.count(owner_predicate), 3)
        self.assertEqual(self.sql.count("return next terminal_owner"), 2)

    def test_runner_claim_is_unique_per_event(self) -> None:
        self.assertIn("event_id text primary key", self.sql)
        self.assertIn("create or replace function public.claim_event_paper_run", self.sql)
        self.assertIn("lease_expires_at", self.sql)
        self.assertIn("event_paper_trade_event_claims.lease_expires_at <= clock_timestamp()", self.sql)
        self.assertIn("or event_paper_trade_event_claims.lease_expires_at <=", self.sql)

    def test_claims_are_scoped_to_worker_token_and_valid_lease(self) -> None:
        self.assertIn("claim_token uuid not null", self.sql)
        self.assertIn("input_claim_token uuid", self.sql)
        self.assertIn("claim_owner_token <> effective_claim_token", self.sql)
        self.assertIn("claim_lease_expires_at < transition_time", self.sql)
        self.assertIn(
            "event_paper_trade_event_claims.claim_token = excluded.claim_token",
            self.sql,
        )
        self.assertIn("claim_token = claim_owner_token", self.sql)

    def test_terminal_claim_does_not_adopt_the_callers_token(self) -> None:
        claim_function = self.sql.split(
            "create or replace function public.claim_event_paper_run", 1
        )[1].split("drop function if exists public.expire_event_paper_trade_run", 1)[0]
        terminal_branch = claim_function.split(
            "if terminal_analysis_id is not null then", 1
        )[1].split("\n  else\n", 1)[0]
        self.assertIn("terminal_status", terminal_branch)
        self.assertNotIn("input_claim_token", terminal_branch)
        self.assertNotIn("claim_token = excluded.claim_token", terminal_branch)

    def test_expiry_can_transfer_only_an_expired_lease_under_event_lock(self) -> None:
        self.assertIn("claim_lease_expires_at > transition_time", self.sql)
        self.assertIn("and lease_expires_at <= transition_time", self.sql)
        self.assertIn("returning event_paper_trade_runs.* into expired_run", self.sql)

    def test_expiry_uses_database_time_after_event_lock_before_mutation(self) -> None:
        function_sql = self.sql.split(
            "create or replace function public.expire_event_paper_trade_run", 1
        )[1]
        lock_at = function_sql.index("pg_advisory_xact_lock")
        deadline_at = function_sql.index("transition_time < input_confirmation_deadline_at")
        mutation_at = function_sql.index("insert into public.event_paper_trade_runs")
        self.assertLess(lock_at, deadline_at)
        self.assertLess(deadline_at, mutation_at)
        self.assertIn("input_confirmation_deadline_at is null", function_sql)
        self.assertIn(
            "create or replace function public.is_event_confirmation_deadline_reached",
            self.sql,
        )
        self.assertIn(
            "clock_timestamp() >= input_confirmation_deadline_at", self.sql
        )
        self.assertIn("transition_time := clock_timestamp()", function_sql)
        self.assertIn("expired_at = transition_time", function_sql)
        self.assertIn("updated_at = transition_time", function_sql)
        self.assertIn("terminal_transition_at = transition_time", function_sql)
        self.assertNotIn("expired_at = input_expired_at", function_sql)

    def test_reconciliation_ranks_first_terminal_transition_without_status_priority(self) -> None:
        reconciliation = self.sql.split("with ranked_terminal as", 1)[1].split(
            "create unique index", 1
        )[0]
        self.assertIn("terminal_transition_at asc", reconciliation)
        self.assertNotIn("case status when 'paper_executed'", reconciliation)
        self.assertIn("first_seen_at asc", reconciliation)
        self.assertIn("id asc", reconciliation)

    def test_reconciliation_backfill_prefers_status_specific_terminal_time(self) -> None:
        backfill = self.sql.split("set terminal_transition_at = coalesce", 1)[1].split(
            "with ranked_terminal as", 1
        )[0]
        self.assertIn("when status = 'expired_no_trade' then expired_at", backfill)
        self.assertIn("when status = 'paper_executed' then updated_at", backfill)
        self.assertNotIn("paper_order->>'created_at'", backfill)
        self.assertIn("updated_at", backfill)
        self.assertIn("first_seen_at", backfill)

    def test_expiry_before_stale_execution_remains_terminal_winner(self) -> None:
        rows = [
            {
                "id": "b",
                "status": "paper_executed",
                "paper_order_created_at": "2026-08-20T15:44:00+00:00",
                "terminal_transition_at": "2026-08-20T15:46:00+00:00",
                "first_seen_at": "2026-08-20T15:40:00+00:00",
            },
            {
                "id": "a",
                "status": "expired_no_trade",
                "terminal_transition_at": "2026-08-20T15:45:00+00:00",
                "first_seen_at": "2026-08-20T15:41:00+00:00",
            },
        ]
        self.assertEqual(_first_terminal(rows)["status"], "expired_no_trade")

    def test_order_creation_time_cannot_outrank_later_execution_persistence(self) -> None:
        rows = [
            {
                "id": "stale-execution",
                "status": "paper_executed",
                "paper_order_created_at": "2026-08-20T15:40:00+00:00",
                "terminal_transition_at": "2026-08-20T15:46:00+00:00",
                "first_seen_at": "2026-08-20T15:39:00+00:00",
            },
            {
                "id": "expiry",
                "status": "expired_no_trade",
                "paper_order_created_at": "",
                "terminal_transition_at": "2026-08-20T15:45:00+00:00",
                "first_seen_at": "2026-08-20T15:41:00+00:00",
            },
        ]
        self.assertEqual(_first_terminal(rows)["id"], "expiry")

    def test_execution_before_stale_expiry_remains_terminal_winner(self) -> None:
        rows = [
            {
                "id": "a",
                "status": "paper_executed",
                "terminal_transition_at": "2026-08-20T15:44:00+00:00",
                "first_seen_at": "2026-08-20T15:40:00+00:00",
            },
            {
                "id": "b",
                "status": "expired_no_trade",
                "terminal_transition_at": "2026-08-20T15:45:00+00:00",
                "first_seen_at": "2026-08-20T15:41:00+00:00",
            },
        ]
        self.assertEqual(_first_terminal(rows)["status"], "paper_executed")

    def test_same_status_uses_earliest_transition_and_is_idempotent(self) -> None:
        rows = [
            {
                "id": "later",
                "status": "paper_executed",
                "terminal_transition_at": "2026-08-20T15:44:01+00:00",
                "first_seen_at": "2026-08-20T15:40:00+00:00",
            },
            {
                "id": "first",
                "status": "paper_executed",
                "terminal_transition_at": "2026-08-20T15:44:00+00:00",
                "first_seen_at": "2026-08-20T15:41:00+00:00",
            },
        ]
        winner = _first_terminal(rows)
        self.assertEqual(winner["id"], "first")
        self.assertEqual(_first_terminal([winner])["id"], "first")

    def test_event_state_read_prioritizes_terminal_and_excludes_superseded(self) -> None:
        self.assertIn("create or replace function public.get_event_paper_trade_state", self.sql)
        self.assertIn("status <> 'superseded'", self.sql)
        self.assertIn(
            "case when status in ('expired_no_trade', 'paper_executed') then 0 else 1 end",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
