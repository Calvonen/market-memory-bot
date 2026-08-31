from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "trading_system" / "approved_tracked_paper_worker.py"
SERVICE = ROOT / "deploy" / "systemd" / "marketai-approved-paper.service"
AUDIT_MIGRATION = ROOT / "supabase" / "migrations" / "20260903157000_broker_attempt_decision_audit.sql"
ATOMIC_MIGRATION = ROOT / "supabase" / "migrations" / "20260903159000_atomic_portfolio_attempt_reservation.sql"
AUTHORITY_MIGRATION = ROOT / "supabase" / "migrations" / "20260903160000_require_portfolio_lease_for_broker_attempt.sql"


class ApprovedPaperProductionEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.service = SERVICE.read_text(encoding="utf-8")
        cls.audit_sql = AUDIT_MIGRATION.read_text(encoding="utf-8")
        cls.atomic_sql = ATOMIC_MIGRATION.read_text(encoding="utf-8")
        cls.authority_sql = AUTHORITY_MIGRATION.read_text(encoding="utf-8")

    def test_worker_discovers_only_approved_paper_tasks_and_calls_orchestrator(self) -> None:
        self.assertIn('.eq("state", "approved")', self.worker)
        self.assertIn('.eq("mode", "PAPER")', self.worker)
        self.assertIn("run_approved_tracked_paper_once(", self.worker)
        self.assertNotIn("create_pending(", self.worker)
        self.assertNotIn(".approve(", self.worker)

    def test_approved_task_discovery_pages_past_the_first_batch(self) -> None:
        self.assertIn('.range(offset, offset + page_size - 1)', self.worker)
        self.assertIn('offset += page_size', self.worker)
        self.assertIn('.select("id,tracked_event_id,instrument")', self.worker)
        self.assertNotIn('.limit(limit)', self.worker)

    def test_portfolio_is_refreshed_before_every_task_risk_decision(self) -> None:
        self.assertIn("def _paper_portfolio_for_instrument(", self.worker)
        self.assertIn('.eq("status", "paper_executed")', self.worker)
        self.assertIn('open_positions=base.open_positions + len(orders)', self.worker)
        self.assertIn('cash=max(0.0, base.cash - total_notional)', self.worker)
        self.assertIn('persisted_exposure_pct = (instrument_notional / base.equity) * 100.0', self.worker)
        portfolio_refresh = self.worker.index("portfolio = _paper_portfolio_for_instrument(")
        orchestration = self.worker.index("result = run_approved_tracked_paper_once(")
        self.assertLess(portfolio_refresh, orchestration)

    def test_portfolio_lease_and_broker_attempt_reservation_are_atomic(self) -> None:
        class_start = self.worker.index("class _PortfolioLeasePaperRuns:")
        begin_start = self.worker.index("    def begin_broker_attempt(", class_start)
        atomic_call = self.worker.index(
            '            "begin_event_paper_broker_attempt_with_portfolio_lease",',
            begin_start,
        )
        self.assertGreater(atomic_call, begin_start)
        self.assertIn("for update", self.atomic_sql.lower())
        self.assertIn("where singleton = true", self.atomic_sql)
        self.assertIn("input_portfolio_lease_token", self.atomic_sql)
        self.assertNotIn("_renew_portfolio_lease(", self.worker[begin_start:atomic_call])
        self.assertNotIn("class _PortfolioLeaseBroker:", self.worker)

    def test_direct_broker_attempt_reservation_is_not_service_role_callable(self) -> None:
        self.assertIn(
            "revoke execute on function public.begin_event_paper_broker_attempt(",
            self.authority_sql,
        )
        self.assertIn(
            "grant execute on function public.begin_event_paper_broker_attempt_with_portfolio_lease(",
            self.authority_sql,
        )
        self.assertIn("security definer", self.authority_sql.lower())

    def test_etoro_demo_execution_is_explicit_and_uses_persisted_identity(self) -> None:
        self.assertIn('MARKETAI_PAPER_BROKER', self.worker)
        self.assertIn('{"internal", "etoro_demo"}', self.worker)
        self.assertIn('MARKETAI_ETORO_DEMO_MAX_AMOUNT_USD', self.worker)
        self.assertIn('event.resolved_etoro_instrument_id', self.worker)
        self.assertIn('event.resolved_etoro_symbol', self.worker)
        self.assertIn('EtoroDemoBroker.from_env(', self.worker)
        self.assertIn('pipeline=PaperTradingPipeline(broker=broker)', self.worker)

    def test_etoro_demo_preflight_happens_before_portfolio_execution_authority(self) -> None:
        preflight = self.worker.index("broker.verify_demo_access()")
        claim = self.worker.index("if not _claim_portfolio_lease(")
        self.assertLess(preflight, claim)

    def test_internal_paper_broker_remains_safe_default(self) -> None:
        self.assertIn('DEFAULT_BROKER_MODE = "internal"', self.worker)
        self.assertIn('broker = PaperBroker()', self.worker)

    def test_systemd_runs_the_production_worker(self) -> None:
        self.assertIn("python -m trading_system.approved_tracked_paper_worker", self.service)
        self.assertIn("EnvironmentFile=/home/marko/marketai/.env", self.service)

    def test_broker_attempt_persists_strategy_and_risk_before_io(self) -> None:
        self.assertIn("strategy_payload jsonb", self.audit_sql)
        self.assertIn("risk_payload jsonb", self.audit_sql)
        self.assertIn("input_strategy_payload jsonb", self.audit_sql)
        self.assertIn("input_risk_payload jsonb", self.audit_sql)
        self.assertIn("'strategy', attempt_row.strategy_payload", self.audit_sql)
        self.assertIn("'risk', attempt_row.risk_payload", self.audit_sql)
        self.assertIn(
            "revoke execute on function public.begin_event_paper_broker_attempt(text, uuid, uuid, integer, uuid, uuid, integer)",
            self.audit_sql,
        )


if __name__ == "__main__":
    unittest.main()
