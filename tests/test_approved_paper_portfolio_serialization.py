from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "trading_system" / "approved_tracked_paper_worker.py"
LEASE_MIGRATION = ROOT / "supabase" / "migrations" / "20260903158000_paper_portfolio_execution_lease.sql"
ATOMIC_MIGRATION = ROOT / "supabase" / "migrations" / "20260903159000_atomic_portfolio_attempt_reservation.sql"


class ApprovedPaperPortfolioSerializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.lease_sql = LEASE_MIGRATION.read_text(encoding="utf-8")
        cls.atomic_sql = ATOMIC_MIGRATION.read_text(encoding="utf-8")

    def test_portfolio_snapshot_to_broker_is_account_serialized(self) -> None:
        self.assertIn("claim_paper_portfolio_execution_lease", self.worker)
        self.assertIn("release_paper_portfolio_execution_lease", self.worker)
        self.assertIn("class _PortfolioLeasePaperRuns", self.worker)
        claim = self.worker.index("if not _claim_portfolio_lease(")
        snapshot = self.worker.index("portfolio = _paper_portfolio_for_instrument(")
        wrapper = self.worker.index("lease_aware_runs = _PortfolioLeasePaperRuns(")
        orchestration = self.worker.index("result = run_approved_tracked_paper_once(")
        self.assertLess(claim, snapshot)
        self.assertLess(snapshot, wrapper)
        self.assertLess(wrapper, orchestration)
        self.assertIn("paper_runs=lease_aware_runs", self.worker)
        self.assertIn("finally:\n                    _release_portfolio_lease", self.worker)

    def test_account_lease_and_attempt_reservation_are_one_rpc_transaction(self) -> None:
        begin = self.worker.index("def begin_broker_attempt(")
        self.assertIn('"begin_event_paper_broker_attempt_with_portfolio_lease"', self.worker[begin:])
        self.assertIn("for update", self.atomic_sql.lower())
        self.assertIn("where singleton = true", self.atomic_sql)
        self.assertNotIn("where id = 1", self.atomic_sql)
        self.assertIn("lease_token <> input_portfolio_lease_token", self.atomic_sql)
        self.assertIn("lease_expires_at <= now_value", self.atomic_sql)
        self.assertIn("public.begin_event_paper_broker_attempt(", self.atomic_sql)

    def test_uncertain_started_attempt_blocks_portfolio_execution(self) -> None:
        self.assertIn("def _assert_no_uncertain_broker_attempts(", self.worker)
        self.assertIn('.eq("status", "started")', self.worker)
        self.assertIn('.select("task_id,event_id,started_at")', self.worker)
        self.assertIn("blocked by unresolved broker attempt with uncertain outcome", self.worker)
        guard = self.worker.index("_assert_no_uncertain_broker_attempts(repository)")
        terminal_read = self.worker.index('table("event_paper_trade_runs")', guard)
        self.assertLess(guard, terminal_read)

    def test_completed_unreconciled_attempts_are_in_portfolio_snapshot(self) -> None:
        self.assertIn('table("event_paper_broker_attempts")', self.worker)
        self.assertIn('.eq("status", "completed")', self.worker)
        self.assertIn('.select("task_id,order_payload,completed_at")', self.worker)
        self.assertIn("orders_by_task[task_id] = order", self.worker)
        self.assertIn("PAPER run and completed broker attempt disagree", self.worker)

    def test_portfolio_lease_rpcs_are_service_role_only(self) -> None:
        self.assertIn("create table if not exists public.paper_portfolio_execution_lease", self.lease_sql)
        self.assertIn("grant execute on function public.claim_paper_portfolio_execution_lease", self.lease_sql)
        self.assertIn("grant execute on function public.release_paper_portfolio_execution_lease", self.lease_sql)
        self.assertIn(
            "grant execute on function public.begin_event_paper_broker_attempt_with_portfolio_lease",
            self.atomic_sql,
        )

    def test_paper_order_pagination_has_unique_tie_breaker(self) -> None:
        self.assertIn('.order("updated_at")\n            .order("id")', self.worker)
        self.assertIn('.select("id,task_id,paper_order")', self.worker)
        self.assertIn('.order("completed_at")\n            .order("task_id")', self.worker)

    def test_negative_baseline_exposure_fails_closed(self) -> None:
        self.assertIn("or exposure_pct < 0", self.worker)
        self.assertIn("instrument exposure must be non-negative", self.worker)


if __name__ == "__main__":
    unittest.main()
