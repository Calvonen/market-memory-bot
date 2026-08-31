from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "trading_system" / "approved_tracked_paper_worker.py"
MIGRATION = ROOT / "supabase" / "migrations" / "20260903158000_paper_portfolio_execution_lease.sql"


class ApprovedPaperPortfolioSerializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_portfolio_snapshot_to_broker_is_account_serialized(self) -> None:
        self.assertIn("claim_paper_portfolio_execution_lease", self.worker)
        self.assertIn("renew_paper_portfolio_execution_lease", self.worker)
        self.assertIn("release_paper_portfolio_execution_lease", self.worker)
        self.assertIn("class _PortfolioLeaseBroker", self.worker)
        claim = self.worker.index("if not _claim_portfolio_lease(")
        snapshot = self.worker.index("portfolio = _paper_portfolio_for_instrument(")
        orchestration = self.worker.index("result = run_approved_tracked_paper_once(")
        self.assertLess(claim, snapshot)
        self.assertLess(snapshot, orchestration)
        self.assertIn("finally:\n                    _release_portfolio_lease", self.worker)

    def test_broker_revalidates_account_lease_immediately_before_execution(self) -> None:
        execute = self.worker.index("def execute(self, proposal: TradeProposal) -> BrokerOrder:")
        renew = self.worker.index("_renew_portfolio_lease(", execute)
        broker = self.worker.index("return self._broker.execute(proposal)", execute)
        self.assertLess(renew, broker)

    def test_portfolio_lease_rpcs_are_service_role_only(self) -> None:
        self.assertIn("create table if not exists public.paper_portfolio_execution_lease", self.sql)
        self.assertIn("where paper_portfolio_execution_lease.lease_token = input_lease_token", self.sql)
        self.assertIn("or paper_portfolio_execution_lease.lease_expires_at <= now_value", self.sql)
        self.assertIn("and lease_expires_at > now_value", self.sql)
        self.assertIn("grant execute on function public.claim_paper_portfolio_execution_lease", self.sql)
        self.assertIn("grant execute on function public.renew_paper_portfolio_execution_lease", self.sql)
        self.assertIn("grant execute on function public.release_paper_portfolio_execution_lease", self.sql)

    def test_paper_order_pagination_has_unique_tie_breaker(self) -> None:
        self.assertIn('.order("updated_at")\n            .order("id")', self.worker)
        self.assertIn('.select("id,paper_order")', self.worker)

    def test_negative_baseline_exposure_fails_closed(self) -> None:
        self.assertIn("or exposure_pct < 0", self.worker)
        self.assertIn("instrument exposure must be non-negative", self.worker)


if __name__ == "__main__":
    unittest.main()
