import unittest
from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20260820100000_atomic_paper_run_transitions.sql"
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

    def test_expiry_can_transfer_only_an_expired_lease_under_event_lock(self) -> None:
        self.assertIn("claim_lease_expires_at > clock_timestamp()", self.sql)
        self.assertIn("and lease_expires_at <= clock_timestamp()", self.sql)
        self.assertIn("returning event_paper_trade_runs.* into expired_run", self.sql)

    def test_event_state_read_prioritizes_terminal_and_excludes_superseded(self) -> None:
        self.assertIn("create or replace function public.get_event_paper_trade_state", self.sql)
        self.assertIn("status <> 'superseded'", self.sql)
        self.assertIn(
            "case when status in ('expired_no_trade', 'paper_executed') then 0 else 1 end",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
