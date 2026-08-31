from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "trading_system" / "approved_tracked_paper_worker.py"
SERVICE = ROOT / "deploy" / "systemd" / "marketai-approved-paper.service"
MIGRATION = ROOT / "supabase" / "migrations" / "20260903157000_broker_attempt_decision_audit.sql"


class ApprovedPaperProductionEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.service = SERVICE.read_text(encoding="utf-8")
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_worker_discovers_only_approved_paper_tasks_and_calls_orchestrator(self) -> None:
        self.assertIn('.eq("state", "approved")', self.worker)
        self.assertIn('.eq("mode", "PAPER")', self.worker)
        self.assertIn("run_approved_tracked_paper_once(", self.worker)
        self.assertNotIn("create_pending(", self.worker)
        self.assertNotIn(".approve(", self.worker)

    def test_systemd_runs_the_production_worker(self) -> None:
        self.assertIn("python -m trading_system.approved_tracked_paper_worker", self.service)
        self.assertIn("EnvironmentFile=/home/marko/marketai/.env", self.service)

    def test_broker_attempt_persists_strategy_and_risk_before_io(self) -> None:
        self.assertIn("strategy_payload jsonb", self.sql)
        self.assertIn("risk_payload jsonb", self.sql)
        self.assertIn("input_strategy_payload jsonb", self.sql)
        self.assertIn("input_risk_payload jsonb", self.sql)
        self.assertIn("'strategy', attempt_row.strategy_payload", self.sql)
        self.assertIn("'risk', attempt_row.risk_payload", self.sql)
        self.assertIn(
            "revoke execute on function public.begin_event_paper_broker_attempt(text, uuid, uuid, integer, uuid, uuid, integer)",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
